#!/usr/bin/env python3
# AFCProject Automated Filament Changer Software
#
# Copyright (C) 2026 AFCProject
#
# This file may be distributed under the terms of the GNU GPLv3 license.

"""
Resync the vendored INDX Klipper macros (templates/indx_macros) with upstream INDX.

The vendored files carry AFC patches marked with `# AFC-PATCH:`. The pristine upstream copy they
were made from lives in templates/indx_macros/.upstream and is the merge base, so a resync is a
3-way merge (git merge-file) of upstream changes into the patched files.

Usage:
    utilities/indx_resync.py merge <path-to-INDX-checkout> [ref]   # default ref: origin/main
    utilities/indx_resync.py check
    utilities/indx_resync.py accept <path-to-INDX-checkout> <ref>  # after fixing conflicts

`merge` leaves conflict markers in place and exits 1 when a file does not merge cleanly. Fix
the conflicts, then run `accept` with the same ref to run the checks and record the new base.
The manual process is described in docs/INDX_macro_resync.md.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MACRO_DIR = ROOT / "templates" / "indx_macros"
UPSTREAM_SUBDIR = "macros"
MARKER = "# AFC-PATCH:"

# Commands AFC owns that may not come back as INDX macros
AFC_OWNED = {"CHANGE_TOOL", "M104", "M109"}
# T(n) wrappers are vendored on purpose, AFC claims the command through `map`
ALLOWED_MACROS = re.compile(r"^T\d+$")

SECTION_RE = re.compile(r"^\[(\w+)(?:\s+([^\]]+))?\]")
REGISTER_RE = re.compile(
    r"register_(?:mux_)?commands?\(\s*(?:[^,()'\"]+,\s*)?['\"]([A-Z][A-Z0-9_]+)['\"]")


@dataclass
class Report:
    """
    Result of the post merge checks: errors fail the resync, notes are informational.
    """
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        """
        :return bool: True if no check failed
        """
        return not self.errors


def read_upstream_record(macro_dir: Path) -> dict[str, str]:
    """
    Parses templates/indx_macros/UPSTREAM (key: value lines).

    :param macro_dir: Vendored macro directory
    :return dict: Parsed keys
    """
    record = {}
    path = macro_dir / "UPSTREAM"
    if path.exists():
        for line in path.read_text().splitlines():
            if (":" in line
                and not line.startswith("#")):
                key, value = line.split(":", 1)
                record[key.strip()] = value.strip()
    return record


def write_upstream_record(macro_dir: Path, repo: str, commit: str) -> None:
    """
    Writes templates/indx_macros/UPSTREAM.

    :param macro_dir: Vendored macro directory
    :param repo: Upstream repo URL
    :param commit: Upstream commit hash the .upstream copy was taken from
    """
    text = ("# Upstream INDX macros the files in this directory were vendored from.\n"
            "# Managed by utilities/indx_resync.py, see docs/INDX_macro_resync.md.\n"
            f"repo: {repo}\n"
            f"commit: {commit}\n"
            f"path: {UPSTREAM_SUBDIR}\n")
    (macro_dir / "UPSTREAM").write_text(text)


def git(indx: Path, *args: str) -> str:
    """
    Runs git in the INDX checkout.

    :param indx: INDX checkout
    :return str: stdout
    """
    result = subprocess.run(["git", "-C", str(indx), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout


def export_upstream(indx: Path, ref: str, dest: Path) -> list[str]:
    """
    Writes macros/*.cfg at `ref` from the INDX checkout into dest.

    :param indx: INDX checkout
    :param ref: Git ref to export
    :param dest: Directory to write into
    :return list: Exported file names
    """
    names = [Path(p).name for p in git(indx, "ls-tree", "--name-only", f"{ref}:{UPSTREAM_SUBDIR}")
             .split() if p.endswith(".cfg")]
    for name in names:
        (dest / name).write_text(git(indx, "show", f"{ref}:{UPSTREAM_SUBDIR}/{name}"))
    return names


def count_markers(macro_dir: Path) -> dict[str, int]:
    """
    :param macro_dir: Vendored macro directory
    :return dict: AFC-PATCH marker count per vendored file
    """
    return {p.name: p.read_text().count(MARKER) for p in sorted(macro_dir.glob("*.cfg"))}


def afc_command_names(root: Path) -> set[str]:
    """
    Command and macro names AFC registers, used to find INDX macros that would collide.

    :param root: AFC repo root
    :return set: Command names
    """
    names = set(AFC_OWNED)
    for path in (root / "extras").glob("*.py"):
        names.update(REGISTER_RE.findall(path.read_text()))
    for path in (root / "config").rglob("*.cfg"):
        for line in path.read_text().splitlines():
            match = SECTION_RE.match(line)
            if (match
                and match.group(1) == "gcode_macro"
                and match.group(2)):
                names.add(match.group(2).strip().upper())
    return names


def macro_sections(text: str) -> list[tuple[str, list[str]]]:
    """
    Splits a cfg file into gcode_macro sections.

    :param text: File contents
    :return list: (macro name, body lines) per gcode_macro section
    """
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            current = None
            if (match.group(1) == "gcode_macro"
                and match.group(2)):
                current = []
                sections.append((match.group(2).strip(), current))
            continue
        if current is not None:
            current.append(line)
    return sections


def user_variables(text: str) -> set[str]:
    """
    :param text: indx.cfg contents
    :return set: variable_ names defined in indx.cfg
    """
    return set(re.findall(r"^variable_(\w+)\s*:", text, re.MULTILINE))


def run_checks(macro_dir: Path, root: Path, markers_before: dict[str, int] | None = None,
               base_dir: Path | None = None, new_dir: Path | None = None) -> Report:
    """
    Post merge checks on the vendored macros.

    :param macro_dir: Vendored macro directory
    :param root: AFC repo root
    :param markers_before: AFC-PATCH counts before the merge, None to skip that check
    :param base_dir: Previous upstream copy, used to report new indx.cfg variables
    :param new_dir: New upstream copy, used to report new indx.cfg variables
    :return Report: Errors and notes
    """
    report = Report()
    afc_names = afc_command_names(root)

    for path in sorted(macro_dir.glob("*.cfg")):
        text = path.read_text()
        if re.search(r"^(<<<<<<<|>>>>>>>)", text, re.MULTILINE):
            report.errors.append(f"{path.name}: unresolved conflict markers")

        if markers_before is not None:
            before = markers_before.get(path.name, 0)
            after = text.count(MARKER)
            if after < before:
                report.errors.append(f"{path.name}: {before - after} AFC-PATCH marker(s) lost "
                                     f"({before} -> {after}), re-apply the dropped patch")

        for name, body in macro_sections(text):
            upper = name.upper()
            if (upper in afc_names
                and not ALLOWED_MACROS.match(upper)):
                report.errors.append(f"{path.name}: [gcode_macro {name}] collides with an AFC "
                                     "command, rename it (and its callers) with an AFC-PATCH")

            if (name != "_INDX_SET_ACTIVE_TOOL"
                and any(re.search(r"SAVE_VARIABLE\s+VARIABLE=active_tool\b", line)
                        for line in body)):
                report.errors.append(f"{path.name}: [gcode_macro {name}] writes active_tool "
                                     "directly, route it through _INDX_SET_ACTIVE_TOOL so AFC "
                                     "is notified")

            for line in body:
                # Blank lines and comments can not call anything
                if (not line.strip()
                    or line.lstrip().startswith("#")):
                    continue
                if (re.search(r"(?<![\w])CHANGE_TOOL\s+TOOL=", line)
                    or re.search(r"^\s*CHANGE_TOOL\b", line)):
                    report.errors.append(f"{path.name}: [gcode_macro {name}] calls CHANGE_TOOL, "
                                         "use INDX_CHANGE_TOOL")

        if path.name == "indx-tc-macros.cfg":
            for name, body in macro_sections(text):
                if name == "_INDX_SET_ACTIVE_TOOL":
                    if not any("AFC_INDX_TOOL_CHANGED" in line for line in body):
                        report.errors.append("indx-tc-macros.cfg: _INDX_SET_ACTIVE_TOOL no longer "
                                             "calls AFC_INDX_TOOL_CHANGED")
                if name == "_TC_LATCH_MOVE":
                    if not any("AFC_INDX_PRE_LATCH" in line for line in body):
                        report.errors.append("indx-tc-macros.cfg: _TC_LATCH_MOVE no longer "
                                             "calls AFC_INDX_PRE_LATCH")

    if (base_dir is not None
        and new_dir is not None
        and (base_dir / "indx.cfg").exists()
        and (new_dir / "indx.cfg").exists()):
        old = user_variables((base_dir / "indx.cfg").read_text())
        new = user_variables((new_dir / "indx.cfg").read_text())
        for var in sorted(new - old):
            report.notes.append(f"indx.cfg: new user setting variable_{var}, mention it in the "
                                "CHANGELOG, users must add it to their own indx.cfg")
        for var in sorted(old - new):
            report.notes.append(f"indx.cfg: variable_{var} removed upstream")
    return report


def print_report(report: Report) -> None:
    """
    Prints notes and errors.

    :param report: Check results
    """
    for note in report.notes:
        print(f"NOTE  {note}")
    for err in report.errors:
        print(f"ERROR {err}")


def accept(macro_dir: Path, new_dir: Path, repo: str, commit: str) -> None:
    """
    Records new_dir as the merge base for the next resync.

    :param macro_dir: Vendored macro directory
    :param new_dir: Upstream files to record
    :param repo: Upstream repo URL
    :param commit: Upstream commit hash
    """
    base = macro_dir / ".upstream"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    for path in new_dir.glob("*.cfg"):
        shutil.copy2(path, base / path.name)
    write_upstream_record(macro_dir, repo, commit)


def cmd_merge(indx: Path, ref: str, macro_dir: Path, root: Path) -> int:
    """
    3-way merges upstream `ref` into the vendored macros.

    :param indx: INDX checkout
    :param ref: Upstream ref to merge
    :param macro_dir: Vendored macro directory
    :param root: AFC repo root
    :return int: Exit code
    """
    base_dir = macro_dir / ".upstream"
    record = read_upstream_record(macro_dir)
    commit = git(indx, "rev-parse", ref).strip()
    if commit == record.get("commit"):
        print(f"Already at {commit}, nothing to merge.")
        return 0
    repo = git(indx, "remote", "get-url", "origin").strip() if record.get("repo") is None \
        else record["repo"]

    markers_before = count_markers(macro_dir)
    conflicts = 0
    with tempfile.TemporaryDirectory() as tmp:
        new_dir = Path(tmp)
        names = export_upstream(indx, ref, new_dir)
        base_names = {p.name for p in base_dir.glob("*.cfg")}
        for name in sorted(set(names) - base_names):
            print(f"NOTE  {name}: new upstream file, not vendored. Add it by hand if AFC needs it.")
        for name in sorted(base_names - set(names)):
            print(f"NOTE  {name}: removed upstream, decide whether to drop the vendored copy.")

        for name in sorted(set(names) & base_names):
            result = subprocess.run(
                ["git", "merge-file", "-L", "AFC", "-L", "base", "-L", f"upstream {ref}",
                 str(macro_dir / name), str(base_dir / name), str(new_dir / name)],
                capture_output=True, text=True)
            if (result.returncode < 0
                or result.returncode > 127):
                print(f"ERROR {name}: git merge-file failed: {result.stderr.strip()}")
                return 2
            if result.returncode:
                conflicts += result.returncode
                print(f"CONFLICT {name}: {result.returncode} conflict(s)")
            else:
                print(f"MERGED {name}")

        report = run_checks(macro_dir, root, markers_before, base_dir, new_dir)
        print_report(report)
        if (conflicts
            or not report.ok()):
            print(f"\nResync to {commit[:12]} incomplete. Fix the above, then run:\n"
                  f"  utilities/indx_resync.py accept {indx} {ref}")
            return 1
        accept(macro_dir, new_dir, repo, commit)
    print(f"\nResynced to {commit}. Review `git diff templates/indx_macros` and add a "
          "CHANGELOG entry.")
    return 0


def cmd_accept(indx: Path, ref: str, macro_dir: Path, root: Path) -> int:
    """
    Records `ref` as the new base after conflicts were resolved by hand.

    :param indx: INDX checkout
    :param ref: Upstream ref that was merged
    :param macro_dir: Vendored macro directory
    :param root: AFC repo root
    :return int: Exit code
    """
    record = read_upstream_record(macro_dir)
    commit = git(indx, "rev-parse", ref).strip()
    repo = record.get("repo") or git(indx, "remote", "get-url", "origin").strip()
    with tempfile.TemporaryDirectory() as tmp:
        new_dir = Path(tmp)
        export_upstream(indx, ref, new_dir)
        report = run_checks(macro_dir, root, None, macro_dir / ".upstream", new_dir)
        print_report(report)
        if not report.ok():
            return 1
        accept(macro_dir, new_dir, repo, commit)
    print(f"Recorded {commit} as the vendored INDX base.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    :param argv: Arguments, defaults to sys.argv
    :return int: Exit code
    """
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--macro-dir", type=Path, default=MACRO_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)
    merge = sub.add_parser("merge", help="merge upstream changes into the vendored macros")
    merge.add_argument("indx", type=Path)
    merge.add_argument("ref", nargs="?", default="origin/main")
    sub.add_parser("check", help="run the post merge checks only")
    acc = sub.add_parser("accept", help="record a ref as the base after fixing conflicts")
    acc.add_argument("indx", type=Path)
    acc.add_argument("ref")
    args = parser.parse_args(argv)

    if args.cmd == "merge":
        return cmd_merge(args.indx, args.ref, args.macro_dir, args.root)
    if args.cmd == "accept":
        return cmd_accept(args.indx, args.ref, args.macro_dir, args.root)
    report = run_checks(args.macro_dir, args.root)
    print_report(report)
    if report.ok():
        print("OK")
        return 0
    print("FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
