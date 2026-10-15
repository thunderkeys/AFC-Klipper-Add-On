# Tests for utilities/indx_tools.py (setting the number of INDX tools).

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "utilities" / "indx_tools.py"
_SPEC = importlib.util.spec_from_file_location("indx_tools", SCRIPT)
tools = importlib.util.module_from_spec(_SPEC)
sys.modules["indx_tools"] = tools
_SPEC.loader.exec_module(tools)


@pytest.fixture
def afc_dir(tmp_path):
    (tmp_path / "indx").mkdir()
    shutil.copy(ROOT / "templates" / "AFC_Hardware_INDX.cfg", tmp_path / "AFC_Hardware.cfg")
    shutil.copy(ROOT / "templates" / "indx_macros" / "indx.cfg", tmp_path / "indx" / "indx.cfg")
    return tmp_path


def _hw(d):
    return (d / "AFC_Hardware.cfg").read_text()


def _indx(d):
    return (d / "indx" / "indx.cfg").read_text()


def _extruder_sections(text):
    return re.findall(r"^\[(AFC_extruder [^\]]+)\]", text, re.M)


def _wrapper_sections(text):
    return re.findall(r"^\[(gcode_macro T\d+)\]", text, re.M)


TOOL = ("[AFC_extruder tool{i}]\n"
        "extruder_name: extruder\n"
        "tool_index: {i}\n"
        "map: T{i}\n")


def _hardware(*indexes, trailing="\n"):
    head = "[AFC_Toolchanger INDX]\nbackend: indx\n\n"
    return head + "\n".join(TOOL.format(i=i) for i in indexes) + trailing


INDX = ("[gcode_macro TOOL_POSITIONS]\n"
        "variable_tool_count: {count}\n"
        "variable_docks_calibrated: {calibrated}\n"
        "{positions}"
        "gcode:\n"
        "\n"
        "{wrappers}")


def _indx_cfg(count, positions, wrappers=(), calibrated="False"):
    pos = "".join(f"variable_t{i}_x: {x}\nvariable_t{i}_dock_y: {y}\n"
                  for i, (x, y) in positions.items())
    wraps = "".join(f"[gcode_macro T{i}]\nvariable_active: False\ngcode: X\n" for i in wrappers)
    return INDX.format(count=count, calibrated=calibrated, positions=pos, wrappers=wraps)


# ── split_sections ───────────────────────────────────────────────────────────

class TestSplitSections:
    def test_preamble_and_sections(self):
        lines = ["# head\n", "[a]\n", "x: 1\n", "[b c]\n"]
        assert tools.split_sections(lines) == [(None, ["# head\n"]),
                                               ("a", ["[a]\n", "x: 1\n"]),
                                               ("b c", ["[b c]\n"])]

    def test_empty(self):
        assert tools.split_sections([]) == [(None, [])]


# ── option ───────────────────────────────────────────────────────────────────

class TestOption:
    def test_found_with_comment(self):
        assert tools.option(["[s]\n", "map: T3   # slicer tool\n"], "map") == "T3"

    def test_equals_separator(self):
        assert tools.option(["[s]\n", "map = T3\n"], "map") == "T3"

    def test_header_not_read(self):
        assert tools.option(["map: T3\n"], "map") is None

    def test_missing(self):
        assert tools.option(["[s]\n", "other: 1\n"], "map") is None


# ── trailing_blank ───────────────────────────────────────────────────────────

class TestTrailingBlank:
    def test_counts_blank_and_comment_lines(self):
        assert tools.trailing_blank(["[s]\n", "a: 1\n", "# note\n", "\n"]) == 2

    def test_none(self):
        assert tools.trailing_blank(["[s]\n", "a: 1\n"]) == 0

    def test_all_blank(self):
        assert tools.trailing_blank(["\n", "#\n"]) == 2


# ── indx_tools ───────────────────────────────────────────────────────────────

class TestIndxTools:
    def test_maps_tool_index_to_block(self):
        blocks = tools.split_sections(_hardware(0, 2).splitlines(keepends=True))
        assert tools.indx_tools(blocks) == {0: 2, 2: 3}

    def test_ignores_sections_without_tool_index(self):
        blocks = tools.split_sections(["[AFC_extruder extruder]\n", "tool_stn: 72\n"])
        assert tools.indx_tools(blocks) == {}

    def test_ignores_other_sections_with_tool_index(self):
        """tool_index alone does not make a non AFC_extruder section a tool."""
        blocks = tools.split_sections(["[other x]\n", "tool_index: 1\n"])
        assert tools.indx_tools(blocks) == {}


# ── new_tool_block ───────────────────────────────────────────────────────────

class TestNewToolBlock:
    def test_copies_with_new_index_and_map(self):
        template = TOOL.format(i=0).splitlines(keepends=True) + ["tool_stn: 55\n", "# x\n", "\n"]
        assert tools.new_tool_block(template, 4) == [
            "[AFC_extruder tool4]\n", "extruder_name: extruder\n", "tool_index: 4\n",
            "map: T4\n", "tool_stn: 55\n", "\n"]


# ── update_hardware ──────────────────────────────────────────────────────────

class TestUpdateHardware:
    def test_adds_after_last_kept_tool(self):
        text, changes = tools.update_hardware(_hardware(0, 1), 3)
        assert _extruder_sections(text) == ["AFC_extruder tool0", "AFC_extruder tool1",
                                             "AFC_extruder tool2"]
        assert changes == ["added [AFC_extruder tool2] (map T2)"]
        assert text.startswith("[AFC_Toolchanger INDX]\nbackend: indx\n\n")

    def test_removes_tools_at_or_above_count(self):
        text, changes = tools.update_hardware(_hardware(0, 1, 2), 1)
        assert _extruder_sections(text) == ["AFC_extruder tool0"]
        assert changes == ["removed [AFC_extruder tool1]", "removed [AFC_extruder tool2]"]

    def test_no_change(self):
        original = _hardware(0, 1)
        text, changes = tools.update_hardware(original, 2)
        assert changes == []
        assert text == original

    def test_no_kept_tool_inserts_after_last(self):
        """Only tools above the count exist: new tools go after the last section."""
        text, changes = tools.update_hardware(_hardware(2), 1)
        assert _extruder_sections(text) == ["AFC_extruder tool0"]
        assert changes == ["removed [AFC_extruder tool2]", "added [AFC_extruder tool0] (map T0)"]

    def test_file_starting_with_section(self):
        """An empty preamble block (file starts with a header) is kept as is."""
        text, _ = tools.update_hardware(TOOL.format(i=0), 2)
        assert text.startswith("[AFC_extruder tool0]\n")
        assert _extruder_sections(text) == ["AFC_extruder tool0", "AFC_extruder tool1"]

    def test_file_without_trailing_newline(self):
        text, _ = tools.update_hardware(_hardware(0).rstrip("\n"), 2)
        assert "map: T0\n\n[AFC_extruder tool1]\n" in text

    def test_no_tools(self):
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.update_hardware("[AFC_Toolchanger INDX]\nbackend: indx\n", 3)
        assert str(exc.value) == ("no [AFC_extruder] sections with tool_index found in "
                                  "AFC_Hardware.cfg")


# ── wrapper_block ────────────────────────────────────────────────────────────

class TestWrapperBlock:
    def test_matches_vendored_wrappers(self):
        vendored = (ROOT / "templates" / "indx_macros" / "indx.cfg").read_text()
        block = "".join(tools.wrapper_block(2))
        assert block in vendored
        assert block == ("[gcode_macro T2]\nvariable_active: False\n"
                         'gcode: RESPOND TYPE=error MSG="T2 is not mapped to an AFC tool, check '
                         'map in AFC_Hardware.cfg"\n')


# ── update_indx ──────────────────────────────────────────────────────────────

class TestUpdateIndx:
    def test_sets_count(self):
        text, changes = tools.update_indx(_indx_cfg(2, {0: (0, -26), 1: (40, -26)}, (0, 1)), 1)
        assert "variable_tool_count: 1\n" in text
        assert changes == ["tool_count 2 -> 1", "removed [gcode_macro T1]"]

    def test_placeholder_spacing_from_last_two_docks(self):
        """t1 x 40, t2 x 100 -> spacing 60, so t3 is at 160."""
        text, _ = tools.update_indx(_indx_cfg(3, {0: (0, -26), 1: (40, -26), 2: (100, -26)},
                                              (0, 1, 2)), 4)
        assert "variable_t3_x:      160.00   # placeholder" in text
        assert "variable_t3_dock_y: -26   # placeholder" in text

    def test_placeholder_default_spacing_with_one_dock(self):
        """A single known dock uses the 45 mm default: 10 + 45 = 55."""
        text, _ = tools.update_indx(_indx_cfg(1, {0: (10, -20)}, (0,)), 2)
        assert "variable_t1_x:      55.00   # placeholder" in text
        assert "variable_t1_dock_y: -20   # placeholder" in text

    def test_placeholder_without_lower_dock(self):
        """No dock below the new one: x 0, dock_y from the lowest known dock."""
        text, _ = tools.update_indx(_indx_cfg(2, {1: (40, -30)}, (0, 1)), 2)
        assert "variable_t0_x:      0.00   # placeholder" in text
        assert "variable_t0_dock_y: -30   # placeholder" in text

    def test_placeholder_dock_y_from_lowest_when_lower_dock_has_none(self):
        """t0 has only x: t1 continues from it (5 + 45) and, with no dock_y anywhere,
        falls back to 0."""
        cfg = INDX.format(count=1, calibrated="False", positions="variable_t0_x: 5\n",
                          wrappers="")
        text, _ = tools.update_indx(cfg, 2)
        assert "variable_t1_x:      50.00   # placeholder" in text
        assert "variable_t1_dock_y: 0   # placeholder" in text

    def test_adding_dock_clears_calibration(self):
        text, changes = tools.update_indx(
            _indx_cfg(1, {0: (0, -26)}, (0,), calibrated="True"), 2)
        assert "variable_docks_calibrated: False\n" in text
        assert changes == [
            "tool_count 1 -> 2",
            "added placeholder dock positions for T1",
            "docks_calibrated set to False: measure the new docks, then set it back to True",
            "added [gcode_macro T(n)] wrappers for T1"]

    def test_existing_docks_keep_calibration(self):
        text, changes = tools.update_indx(
            _indx_cfg(1, {0: (0, -26), 1: (40, -26)}, (0,), calibrated="True"), 2)
        assert "variable_docks_calibrated: True\n" in text
        assert changes == ["tool_count 1 -> 2", "added [gcode_macro T(n)] wrappers for T1"]

    def test_dock_with_only_x_gets_dock_y_only(self):
        """The existing t1_x is kept and not written twice."""
        cfg = _indx_cfg(1, {0: (0, -26)}, (0,)).replace(
            "gcode:\n", "variable_t1_x: 40\ngcode:\n")
        text, changes = tools.update_indx(cfg, 2)
        assert "added placeholder dock positions for T1" in changes
        assert len(re.findall(r"^variable_t1_x:", text, re.M)) == 1
        assert "variable_t1_x: 40\n" in text
        assert "variable_t1_dock_y: -26   # placeholder" in text

    def test_dock_with_only_dock_y_gets_x_only(self):
        """t1 has dock_y -30 only: x is placeholdered from t0 (10 + 45), dock_y kept."""
        cfg = _indx_cfg(1, {0: (10, -26)}, (0,)).replace(
            "gcode:\n", "variable_t1_dock_y: -30\ngcode:\n")
        text, _ = tools.update_indx(cfg, 2)
        assert len(re.findall(r"^variable_t1_dock_y:", text, re.M)) == 1
        assert "variable_t1_x:      55.00   # placeholder" in text

    def test_wrapper_appended_without_trailing_newline(self):
        cfg = _indx_cfg(1, {0: (0, -26)}, (0,)).rstrip("\n")
        text, _ = tools.update_indx(cfg, 2)
        assert "gcode: X\n[gcode_macro T1]\n" in text

    def test_no_change(self):
        original = _indx_cfg(2, {0: (0, -26), 1: (40, -26)}, (0, 1))
        text, changes = tools.update_indx(original, 2)
        assert changes == []
        assert text == original

    def test_missing_tool_count(self):
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.update_indx("variable_t0_x: 0\n", 2)
        assert str(exc.value) == "variable_tool_count not found in indx/indx.cfg"

    def test_missing_dock_positions(self):
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.update_indx("variable_tool_count: 1\n", 2)
        assert str(exc.value) == "no variable_tN_x / variable_tN_dock_y pairs in indx/indx.cfg"


# ── paths ────────────────────────────────────────────────────────────────────

class TestPaths:
    def test_returns_both(self, afc_dir):
        assert tools.paths(afc_dir) == (afc_dir / "AFC_Hardware.cfg",
                                        afc_dir / "indx" / "indx.cfg")

    def test_missing_hardware(self, tmp_path):
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.paths(tmp_path)
        assert str(exc.value) == (f"{tmp_path / 'AFC_Hardware.cfg'} not found, is AFC installed "
                                  "with Bondtech INDX?")

    def test_missing_indx(self, afc_dir):
        (afc_dir / "indx" / "indx.cfg").unlink()
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.paths(afc_dir)
        assert str(exc.value) == (f"{afc_dir / 'indx' / 'indx.cfg'} not found, is AFC installed "
                                  "with Bondtech INDX?")


# ── set_count ────────────────────────────────────────────────────────────────

class TestSetCount:
    def test_extend_template_to_six(self, afc_dir):
        changes = tools.set_count(afc_dir, 6)
        assert _extruder_sections(_hw(afc_dir)) == [f"AFC_extruder tool{i}" for i in range(6)]
        assert _wrapper_sections(_indx(afc_dir)) == [f"gcode_macro T{i}" for i in range(6)]
        assert "variable_tool_count: 6" in _indx(afc_dir)
        assert changes[0] == "AFC_Hardware.cfg: added [AFC_extruder tool3] (map T3)"
        assert changes[3] == "indx/indx.cfg: tool_count 3 -> 6"

    def test_unchanged_files_not_written(self, afc_dir):
        tools.set_count(afc_dir, 3)
        hw = afc_dir / "AFC_Hardware.cfg"
        hw.chmod(0o444)
        try:
            assert tools.set_count(afc_dir, 3) == []
        finally:
            hw.chmod(0o644)

    def test_only_indx_changes(self, afc_dir):
        """Hardware already matches: only indx.cfg is written."""
        tools.set_count(afc_dir, 3)
        indx = afc_dir / "indx" / "indx.cfg"
        indx.write_text(indx.read_text().replace("variable_tool_count: 3",
                                                 "variable_tool_count: 2"))
        hw = afc_dir / "AFC_Hardware.cfg"
        hw.chmod(0o444)
        try:
            assert tools.set_count(afc_dir, 3) == ["indx/indx.cfg: tool_count 2 -> 3"]
        finally:
            hw.chmod(0o644)

    def test_only_hardware_changes(self, afc_dir):
        """indx.cfg already matches: only AFC_Hardware.cfg is written."""
        tools.set_count(afc_dir, 4)
        hw = afc_dir / "AFC_Hardware.cfg"
        hw.write_text(_hw(afc_dir).split("[AFC_extruder tool3]")[0])
        indx = afc_dir / "indx" / "indx.cfg"
        indx.chmod(0o444)
        try:
            assert tools.set_count(afc_dir, 4) == [
                "AFC_Hardware.cfg: added [AFC_extruder tool3] (map T3)"]
        finally:
            indx.chmod(0o644)

    def test_new_tools_copy_settings(self, afc_dir):
        hw = _hw(afc_dir).replace("[AFC_extruder tool0]\nextruder_name: extruder\n",
                                  "[AFC_extruder tool0]\nextruder_name: extruder\ntool_stn: 55\n")
        (afc_dir / "AFC_Hardware.cfg").write_text(hw)
        tools.set_count(afc_dir, 4)
        block = _hw(afc_dir).split("[AFC_extruder tool3]")[1]
        assert "tool_stn: 55" in block
        assert "tool_index: 3\nmap: T3\n" in block

    @pytest.mark.parametrize("count", [0, 33])
    def test_out_of_range(self, afc_dir, count):
        with pytest.raises(tools.ToolConfigError) as exc:
            tools.set_count(afc_dir, count)
        assert str(exc.value) == "tool count must be between 1 and 32"

    @pytest.mark.parametrize("count", [1, 32])
    def test_range_limits_accepted(self, afc_dir, count):
        tools.set_count(afc_dir, count)
        assert f"variable_tool_count: {count}" in _indx(afc_dir)


# ── show ─────────────────────────────────────────────────────────────────────

class TestShow:
    def test_lists_tools(self, afc_dir):
        assert tools.show(afc_dir) == "indx.cfg tool_count: 3\nAFC tools: T0, T1, T2"

    def test_unknown_tool_count(self, afc_dir):
        indx = afc_dir / "indx" / "indx.cfg"
        indx.write_text(indx.read_text().replace("variable_tool_count: 3", "#"))
        assert tools.show(afc_dir) == "indx.cfg tool_count: ?\nAFC tools: T0, T1, T2"


# ── main ─────────────────────────────────────────────────────────────────────

class TestMain:
    def test_set_count(self, afc_dir, capsys):
        assert tools.main(["--config-dir", str(afc_dir), "set-count", "4"]) == 0
        # The template already has T3 dock positions and a T3 wrapper
        assert capsys.readouterr().out == (
            "- AFC_Hardware.cfg: added [AFC_extruder tool3] (map T3)\n"
            "- indx/indx.cfg: tool_count 3 -> 4\n"
            "Restart Klipper to apply.\n")

    def test_nothing_changed(self, afc_dir, capsys):
        tools.set_count(afc_dir, 3)
        capsys.readouterr()
        assert tools.main(["--config-dir", str(afc_dir), "set-count", "3"]) == 0
        assert capsys.readouterr().out == "Already configured for 3 tools, nothing changed.\n"

    def test_show(self, afc_dir, capsys):
        assert tools.main(["--config-dir", str(afc_dir), "show"]) == 0
        assert capsys.readouterr().out == "indx.cfg tool_count: 3\nAFC tools: T0, T1, T2\n"

    def test_error(self, afc_dir, capsys):
        assert tools.main(["--config-dir", str(afc_dir), "set-count", "0"]) == 1
        assert capsys.readouterr().out == "ERROR: tool count must be between 1 and 32\n"


class TestScriptEntryPoint:
    def test_runs_as_script(self, afc_dir):
        result = subprocess.run([sys.executable, str(SCRIPT), "--config-dir", str(afc_dir),
                                 "show"], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout == "indx.cfg tool_count: 3\nAFC tools: T0, T1, T2\n"

    def test_main_guard_exits_with_main_result(self, afc_dir, monkeypatch, capsys):
        import runpy
        monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--config-dir", str(afc_dir),
                                          "set-count", "0"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exc.value.code == 1
        assert capsys.readouterr().out == "ERROR: tool count must be between 1 and 32\n"
