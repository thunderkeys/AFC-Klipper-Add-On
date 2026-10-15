# Tests for utilities/indx_tools.py (setting the number of INDX tools).

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("indx_tools", ROOT / "utilities" / "indx_tools.py")
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


def _sections(text, prefix):
    import re
    return [line.strip() for line in text.splitlines()
            if (line.startswith(f"[{prefix}")
                and (prefix != "gcode_macro T"
                     or re.match(r"^\[gcode_macro T\d+\]", line)))]


def test_extend_to_six(afc_dir):
    changes = tools.set_count(afc_dir, 6)
    assert changes
    assert _sections(_hw(afc_dir), "AFC_extruder") == [
        f"[AFC_extruder tool{i}]" for i in range(6)]
    hw = _hw(afc_dir)
    assert "tool_index: 5\nmap: T5\n" in hw
    indx = _indx(afc_dir)
    assert "variable_tool_count: 6" in indx
    assert _sections(indx, "gcode_macro T") == [f"[gcode_macro T{i}]" for i in range(6)]
    for i in range(6):
        assert f"variable_t{i}_x:" in indx
        assert f"variable_t{i}_dock_y:" in indx


def test_idempotent(afc_dir):
    tools.set_count(afc_dir, 6)
    hw, indx = _hw(afc_dir), _indx(afc_dir)
    assert tools.set_count(afc_dir, 6) == []
    assert _hw(afc_dir) == hw
    assert _indx(afc_dir) == indx


def test_new_tools_copy_existing_tool_settings(afc_dir):
    hw = _hw(afc_dir).replace("[AFC_extruder tool0]\nextruder_name: extruder\n",
                              "[AFC_extruder tool0]\nextruder_name: extruder\ntool_stn: 55\n")
    (afc_dir / "AFC_Hardware.cfg").write_text(hw)
    tools.set_count(afc_dir, 4)
    block = _hw(afc_dir).split("[AFC_extruder tool3]")[1]
    assert "tool_stn: 55" in block
    assert "tool_index: 3" in block
    assert "map: T3" in block


def test_reduce_removes_tools_and_wrappers_keeps_docks(afc_dir):
    tools.set_count(afc_dir, 6)
    tools.set_count(afc_dir, 2)
    assert _sections(_hw(afc_dir), "AFC_extruder") == ["[AFC_extruder tool0]",
                                                      "[AFC_extruder tool1]"]
    indx = _indx(afc_dir)
    assert "variable_tool_count: 2" in indx
    assert _sections(indx, "gcode_macro T") == ["[gcode_macro T0]", "[gcode_macro T1]"]
    assert "variable_t5_x:" in indx
    # Everything before the tool sections is intact
    assert "[AFC_Toolchanger INDX]" in _hw(afc_dir)
    assert "[include indx/homing.cfg]" in _hw(afc_dir)


def test_placeholder_docks_follow_spacing(afc_dir):
    tools.set_count(afc_dir, 6)
    indx = _indx(afc_dir)
    # template: t2 88.70, t3 102.0 -> spacing 13.3
    assert "variable_t4_x:      115.30   # placeholder" in indx
    assert "variable_t5_x:      128.60   # placeholder" in indx
    assert "variable_t5_dock_y: -26" in indx


def test_adding_docks_clears_docks_calibrated(afc_dir):
    indx = _indx(afc_dir).replace("variable_docks_calibrated: False",
                                  "variable_docks_calibrated: True")
    (afc_dir / "indx" / "indx.cfg").write_text(indx)
    changes = tools.set_count(afc_dir, 5)
    assert "variable_docks_calibrated: False" in _indx(afc_dir)
    assert any("docks_calibrated set to False" in c for c in changes)


def test_count_change_without_new_docks_keeps_calibration(afc_dir):
    indx = _indx(afc_dir).replace("variable_docks_calibrated: False",
                                  "variable_docks_calibrated: True")
    (afc_dir / "indx" / "indx.cfg").write_text(indx)
    # template already has t0..t3 positions
    tools.set_count(afc_dir, 4)
    assert "variable_docks_calibrated: True" in _indx(afc_dir)


def test_custom_unit_name_and_comments_preserved(afc_dir):
    hw = _hw(afc_dir).replace("toolchanger_unit: INDX", "toolchanger_unit: Head")
    (afc_dir / "AFC_Hardware.cfg").write_text(hw)
    tools.set_count(afc_dir, 4)
    block = _hw(afc_dir).split("[AFC_extruder tool3]")[1]
    assert "toolchanger_unit: Head" in block
    assert "# led_name is left unset" in _hw(afc_dir)


@pytest.mark.parametrize("count", [0, 33])
def test_out_of_range(afc_dir, count):
    with pytest.raises(tools.ToolConfigError):
        tools.set_count(afc_dir, count)


def test_missing_files(tmp_path):
    with pytest.raises(tools.ToolConfigError, match="not found"):
        tools.set_count(tmp_path, 3)


def test_no_tool_sections(afc_dir):
    (afc_dir / "AFC_Hardware.cfg").write_text("[AFC_Toolchanger INDX]\nbackend: indx\n")
    with pytest.raises(tools.ToolConfigError, match="tool_index"):
        tools.set_count(afc_dir, 3)


def test_cli(afc_dir, capsys):
    assert tools.main(["--config-dir", str(afc_dir), "set-count", "6"]) == 0
    assert "Restart Klipper" in capsys.readouterr().out
    assert tools.main(["--config-dir", str(afc_dir), "show"]) == 0
    assert "T0, T1, T2, T3, T4, T5" in capsys.readouterr().out
    assert tools.main(["--config-dir", str(afc_dir), "set-count", "0"]) == 1


def test_six_tool_config_passes_afc_validation(afc_dir):
    """The generated config satisfies the INDX backend's startup checks."""
    import re
    tools.set_count(afc_dir, 6)
    count = int(re.search(r"^variable_tool_count: (\d+)", _indx(afc_dir), re.M).group(1))
    indexes = [int(i) for i in re.findall(r"^tool_index: (\d+)", _hw(afc_dir), re.M)]
    maps = re.findall(r"^map: (T\d+)", _hw(afc_dir), re.M)
    assert sorted(indexes) == list(range(count))
    assert sorted(maps) == sorted(f"T{i}" for i in range(count))
