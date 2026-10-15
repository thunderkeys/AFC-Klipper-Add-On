#!/usr/bin/env python3
# AFCProject Automated Filament Changer Software
#
# Copyright (C) 2026 AFCProject
#
# This file may be distributed under the terms of the GNU GPLv3 license.

"""
Set the number of Bondtech INDX tools in an installed AFC config.

Usage:
    utilities/indx_tools.py set-count <n> [--config-dir ~/printer_data/config/AFC]
    utilities/indx_tools.py show [--config-dir ~/printer_data/config/AFC]

set-count updates both files that describe the tools:

- AFC_Hardware.cfg: one [AFC_extruder toolN] per tool. New sections copy the settings of the
  lowest numbered existing INDX tool, with tool_index and map changed. Sections for tools at or
  above <n> are removed.
- indx/indx.cfg: tool_count, a placeholder dock position pair (tN_x/tN_dock_y) for each new tool,
  and a [gcode_macro TN] wrapper per tool. Wrappers above <n> are removed, dock positions are
  kept. Adding a tool sets docks_calibrated to False, since the new dock positions are
  placeholders that must be measured before INDX drives into them.

Run it with Klipper stopped or restart Klipper afterwards.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_CONFIG_DIR = Path.home() / "printer_data" / "config" / "AFC"
HARDWARE_FILE = "AFC_Hardware.cfg"
INDX_FILE = Path("indx") / "indx.cfg"
DEFAULT_DOCK_SPACING = 45.0
MAX_TOOLS = 32

SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


class ToolConfigError(Exception):
    """
    Raised when the installed config can not be updated.
    """


Blocks = List[Tuple[Optional[str], List[str]]]


def split_sections(lines: List[str]) -> Blocks:
    """
    Splits cfg lines into (section name, lines) blocks. The first block has name None and
    holds anything before the first section. Each block's lines include its header.

    :param lines: Config file lines, with line endings
    :return list: (section name, lines) per block
    """
    blocks: Blocks = [(None, [])]
    for line in lines:
        match = SECTION_RE.match(line)
        if match:
            blocks.append((match.group(1).strip(), [line]))
        else:
            blocks[-1][1].append(line)
    return blocks


def option(block: List[str], key: str) -> Optional[str]:
    """
    Reads an option from a section block.

    :param block: Section lines, header first
    :param key: Option name
    :return str|None: Value of `key`, None if not set
    """
    for line in block[1:]:
        match = re.match(rf"^{re.escape(key)}\s*[:=]\s*(.*?)\s*(#.*)?$", line)
        if match:
            return match.group(1)
    return None


def trailing_blank(block: List[str]) -> int:
    """
    Counts the blank and comment lines at the end of a block.

    :param block: Section lines
    :return int: Number of trailing blank/comment lines that separate this block from the next
    """
    count = 0
    for line in reversed(block):
        if (line.strip() == ""
            or line.lstrip().startswith("#")):
            count += 1
        else:
            break
    return count


# ── AFC_Hardware.cfg ─────────────────────────────────────────────────────────

def indx_tools(blocks: Blocks) -> Dict[int, int]:
    """
    Finds the INDX tool sections in AFC_Hardware.cfg.

    :param blocks: Blocks from split_sections
    :return dict: tool_index -> block index for [AFC_extruder] sections with a tool_index
    """
    tools: Dict[int, int] = {}
    for i, (name, block) in enumerate(blocks):
        tool_index = option(block, "tool_index")
        if (name
            and name.startswith("AFC_extruder ")
            and tool_index is not None):
            tools[int(tool_index)] = i
    return tools


def new_tool_block(template: List[str], index: int) -> List[str]:
    """
    Copies an INDX tool section for another tool number.

    :param template: Section lines of an existing INDX tool
    :param index: Tool number for the copy
    :return list: Section lines for the new tool
    """
    body = template[1:len(template) - trailing_blank(template)]
    out = [f"[AFC_extruder tool{index}]\n"]
    for line in body:
        if re.match(r"^tool_index\s*[:=]", line):
            line = f"tool_index: {index}\n"
        elif re.match(r"^map\s*[:=]", line):
            line = f"map: T{index}\n"
        out.append(line)
    out.append("\n")
    return out


def update_hardware(text: str, count: int) -> Tuple[str, List[str]]:
    """
    Adds/removes [AFC_extruder] INDX tool sections so tools 0..count-1 exist.

    :param text: AFC_Hardware.cfg contents
    :param count: Number of tools
    :return tuple: (new text, change descriptions)
    """
    blocks = split_sections(text.splitlines(keepends=True))
    tools = indx_tools(blocks)
    if not tools:
        error_str = f"no [AFC_extruder] sections with tool_index found in {HARDWARE_FILE}"
        raise ToolConfigError(error_str)
    changes: List[str] = []

    template = blocks[tools[min(tools)]][1]
    remove = {i for idx, i in tools.items() if idx >= count}
    for idx in sorted(tools):
        if idx >= count:
            changes.append(f"removed [{blocks[tools[idx]][0]}]")

    missing = [i for i in range(count) if i not in tools]
    # New sections go after the last tool that is kept
    kept = [i for idx, i in tools.items() if idx < count]
    insert_after = max(kept) if kept else max(tools.values())
    new_blocks: List[List[str]] = []
    for i, (name, block) in enumerate(blocks):
        if i not in remove:
            if (block
                and not block[-1].endswith("\n")):
                block = block[:-1] + [block[-1] + "\n"]
            new_blocks.append(block)
        # Insert even when the anchor section was removed, after whatever was kept before it
        if i == insert_after:
            # The preamble block is never removed, so new_blocks is never empty here
            if trailing_blank(new_blocks[-1]) == 0:
                new_blocks.append(["\n"])
            for idx in missing:
                new_blocks.append(new_tool_block(template, idx))
                changes.append(f"added [AFC_extruder tool{idx}] (map T{idx})")
    return "".join("".join(b) for b in new_blocks), changes


# ── indx/indx.cfg ────────────────────────────────────────────────────────────

def wrapper_block(index: int) -> List[str]:
    """
    Builds a T(n) wrapper macro like the ones in the vendored indx.cfg.

    :param index: Tool number
    :return list: Section lines for the wrapper
    """
    return [f"[gcode_macro T{index}]\n",
            "variable_active: False\n",
            f'gcode: RESPOND TYPE=error MSG="T{index} is not mapped to an AFC tool, check map in '
            'AFC_Hardware.cfg"\n']


def update_indx(text: str, count: int) -> Tuple[str, List[str]]:
    """
    Sets tool_count, adds placeholder dock positions and T(n) wrappers, removes wrappers at or
    above count.

    :param text: indx.cfg contents
    :param count: Number of tools
    :return tuple: (new text, change descriptions)
    """
    lines = text.splitlines(keepends=True)
    changes: List[str] = []

    count_re = re.compile(r"^(variable_tool_count\s*:\s*)(\d+)(.*)$", re.DOTALL)
    found_count = False
    for i, line in enumerate(lines):
        match = count_re.match(line)
        if match:
            found_count = True
            if int(match.group(2)) != count:
                changes.append(f"tool_count {match.group(2)} -> {count}")
                lines[i] = f"{match.group(1)}{count}{match.group(3)}"
    if not found_count:
        error_str = f"variable_tool_count not found in {INDX_FILE}"
        raise ToolConfigError(error_str)

    # Dock positions
    pos_re = re.compile(r"^variable_t(\d+)_(x|dock_y)\s*:\s*([-\d.]+)")
    positions: Dict[int, Dict[str, float]] = {}
    last_pos_line = -1
    for i, line in enumerate(lines):
        match = pos_re.match(line)
        if match:
            positions.setdefault(int(match.group(1)), {})[match.group(2)] = float(match.group(3))
            last_pos_line = i
    if last_pos_line < 0:
        error_str = f"no variable_tN_x / variable_tN_dock_y pairs in {INDX_FILE}"
        raise ToolConfigError(error_str)

    added_docks: List[int] = []
    insert: List[str] = []
    known = dict(positions)
    for idx in range(count):
        have = known.get(idx, {})
        if ("x" in have
            and "dock_y" in have):
            continue
        prev = [i for i in sorted(known)
                if (i < idx
                    and "x" in known[i])]
        lowest_dock_y = known[min(known)].get("dock_y", 0.0)
        if len(prev) >= 2:
            spacing = known[prev[-1]]["x"] - known[prev[-2]]["x"]
        else:
            spacing = DEFAULT_DOCK_SPACING
        if prev:
            # Continue the spacing of the docks below this one
            x = known[prev[-1]]["x"] + spacing
            dock_y = known[prev[-1]].get("dock_y", lowest_dock_y)
        else:
            # No dock below this one: start at 0 with the depth of the lowest known dock
            x = 0.0
            dock_y = lowest_dock_y
        # Only add what is missing, klipper refuses an option set twice in one section
        insert.append("#\n")
        entry = dict(have)
        if "x" not in have:
            entry["x"] = x
            insert.append(f"variable_t{idx}_x:      {x:.2f}   # placeholder, measure with "
                          f"CALIBRATE_DOCK_X TOOL={idx}\n")
        if "dock_y" not in have:
            entry["dock_y"] = dock_y
            insert.append(f"variable_t{idx}_dock_y: {dock_y:g}   # placeholder, see Finding "
                          "dock_y\n")
        known[idx] = entry
        added_docks.append(idx)
    if insert:
        lines[last_pos_line + 1:last_pos_line + 1] = insert
        changes.append("added placeholder dock positions for "
                       + ", ".join(f"T{i}" for i in added_docks))
        for i, line in enumerate(lines):
            match = re.match(r"^(variable_docks_calibrated\s*:\s*)True(.*)$", line, re.DOTALL)
            if match:
                lines[i] = f"{match.group(1)}False{match.group(2)}"
                changes.append("docks_calibrated set to False: measure the new docks, then set "
                               "it back to True")

    # T(n) wrappers
    blocks = split_sections(lines)
    wrappers: Dict[int, int] = {}
    for i, (name, _) in enumerate(blocks):
        match = re.match(r"^gcode_macro T(\d+)$", name or "")
        if match:
            wrappers[int(match.group(1))] = i
    remove = {i for idx, i in wrappers.items() if idx >= count}
    out_blocks: List[List[str]] = []
    for i, (name, block) in enumerate(blocks):
        if i in remove:
            changes.append(f"removed [{name}]")
            continue
        out_blocks.append(block)
    missing = [i for i in range(count) if i not in wrappers]
    if missing:
        last = out_blocks[-1]
        if (last
            and not last[-1].endswith("\n")):
            out_blocks[-1] = last[:-1] + [last[-1] + "\n"]
        for idx in missing:
            out_blocks.append(wrapper_block(idx))
        changes.append("added [gcode_macro T(n)] wrappers for "
                       + ", ".join(f"T{i}" for i in missing))
    return "".join("".join(b) for b in out_blocks), changes


# ── Commands ─────────────────────────────────────────────────────────────────

def paths(config_dir: Path) -> Tuple[Path, Path]:
    """
    Resolves the two files that describe the tools.

    :param config_dir: AFC config directory
    :return tuple: (AFC_Hardware.cfg path, indx/indx.cfg path)
    """
    hardware = config_dir / HARDWARE_FILE
    indx = config_dir / INDX_FILE
    for path in (hardware, indx):
        if not path.exists():
            error_str = f"{path} not found, is AFC installed with Bondtech INDX?"
            raise ToolConfigError(error_str)
    return hardware, indx


def set_count(config_dir: Path, count: int) -> List[str]:
    """
    Updates both files for `count` tools.

    :param config_dir: AFC config directory
    :param count: Number of tools
    :return list: Change descriptions, empty if nothing changed
    """
    if (count < 1
        or count > MAX_TOOLS):
        error_str = f"tool count must be between 1 and {MAX_TOOLS}"
        raise ToolConfigError(error_str)
    hardware, indx = paths(config_dir)
    hw_text, hw_changes = update_hardware(hardware.read_text(), count)
    indx_text, indx_changes = update_indx(indx.read_text(), count)
    if hw_changes:
        hardware.write_text(hw_text)
    if indx_changes:
        indx.write_text(indx_text)
    return ([f"{HARDWARE_FILE}: {c}" for c in hw_changes]
            + [f"{INDX_FILE}: {c}" for c in indx_changes])


def show(config_dir: Path) -> str:
    """
    Describes the configured tools.

    :param config_dir: AFC config directory
    :return str: tool_count from indx.cfg and the AFC tools found
    """
    hardware, indx = paths(config_dir)
    tools = indx_tools(split_sections(hardware.read_text().splitlines(keepends=True)))
    match = re.search(r"^variable_tool_count\s*:\s*(\d+)", indx.read_text(), re.MULTILINE)
    count = match.group(1) if match else "?"
    listed = ", ".join(f"T{i}" for i in sorted(tools))
    return f"indx.cfg tool_count: {count}\nAFC tools: {listed}"


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command line entry point.

    :param argv: Arguments, defaults to sys.argv
    :return int: Exit code
    """
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR,
                        help="AFC config directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("set-count", help="set the number of INDX tools")
    sc.add_argument("count", type=int)
    sub.add_parser("show", help="show the configured tools")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "show":
            print(show(args.config_dir))
            return 0
        changes = set_count(args.config_dir, args.count)
    except ToolConfigError as e:
        print(f"ERROR: {e}")
        return 1
    if not changes:
        print(f"Already configured for {args.count} tools, nothing changed.")
    else:
        for change in changes:
            print(f"- {change}")
        print("Restart Klipper to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
