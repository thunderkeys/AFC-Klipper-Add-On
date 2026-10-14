# Unit tests for extras/AFC_indx.py

from __future__ import annotations

from configparser import Error as KlipperError
from unittest.mock import MagicMock
import pytest

from extras.AFC_indx import IndxBackend
from extras.AFC import State
from tests.conftest import MockGCodeCommand
from tests.test_AFC_Toolchanger import _make_toolchanger


class FakeSaveVariables:
    def __init__(self, active_tool=-1):
        self.allVariables = {"active_tool": active_tool}

    def get_status(self, eventtime):
        return {"variables": self.allVariables}


class FakeMacro:
    def __init__(self, **variables):
        self.variables = dict(variables)


def _make_tool(unit, name, index, afc):
    tool = MagicMock()
    tool.name = tool.tool_key = name
    tool.fullname = f"AFC_extruder {name}"
    tool.th_extruder_name = "extruder"
    tool.tool_index = index
    tool.tc_unit_obj = unit
    tool.lane_loaded = None
    lane = MagicMock()
    lane.name = name
    lane.extruder_obj = tool
    lane.map = [f"T{index}"]
    tool.tc_lane = lane
    afc.lanes[name] = lane
    afc.tools[name] = tool
    return tool


def _make_indx(tool_count=3, n_tools=3, active=-1, macros=True):
    unit = _make_toolchanger(values={"backend": "indx"})
    backend = unit.backend
    afc = unit.afc
    printer = unit.printer
    save_vars = FakeSaveVariables(active)
    printer._objects["save_variables"] = save_vars
    printer._objects["gcode_macro TOOL_POSITIONS"] = FakeMacro(tool_count=tool_count,
                                                               no_heat_tools=[])
    tools = [_make_tool(unit, f"T{i}", i, afc) for i in range(n_tools)]
    if macros:
        for i in range(n_tools):
            printer._objects[f"gcode_macro T{i}"] = FakeMacro(active=False)
    return unit, backend, save_vars, tools


def _simulate_indx(unit, save_vars, result=None):
    """Make run_script_from_command behave like the INDX macros: CHANGE sets active_tool
    to TOOL (or `result` when given), PARK sets -1."""
    def _run(cmd):
        if cmd.startswith("INDX_CHANGE_TOOL"):
            tool = int(cmd.split("TOOL=")[1].split()[0])
            save_vars.allVariables["active_tool"] = tool if result is None else result
        elif cmd == "PARK_TOOL":
            save_vars.allVariables["active_tool"] = -1 if result is None else result
    unit.afc.gcode.run_script_from_command = MagicMock(side_effect=_run)


class TestBackendFlags:
    def test_is_indx_backend(self):
        unit = _make_toolchanger(values={"backend": "indx"})
        assert isinstance(unit.backend, IndxBackend)
        assert unit.backend.shared_extruder is True
        assert unit.backend.heats_on_pickup is True

    def test_registers_hook_command(self):
        unit = _make_toolchanger(values={"backend": "indx"})
        assert "AFC_INDX_TOOL_CHANGED" in unit.afc.gcode._commands


class TestHandleReady:
    def test_valid_config(self):
        unit, backend, _, _ = _make_indx()
        backend.handle_ready()

    def test_missing_save_variables(self):
        unit, backend, _, _ = _make_indx()
        del unit.printer._objects["save_variables"]
        with pytest.raises(KlipperError, match="requires a \\[save_variables\\]"):
            backend.handle_ready()

    def test_missing_indx_macros(self):
        unit, backend, _, _ = _make_indx()
        del unit.printer._objects["gcode_macro TOOL_POSITIONS"]
        with pytest.raises(KlipperError, match="requires the INDX macros"):
            backend.handle_ready()

    def test_missing_tool_index(self):
        unit, backend, _, tools = _make_indx()
        tools[1].tool_index = None
        with pytest.raises(KlipperError, match="tool_index is required"):
            backend.handle_ready()

    def test_tool_index_above_tool_count(self):
        unit, backend, _, tools = _make_indx(tool_count=2, n_tools=2)
        tools[1].tool_index = 2
        with pytest.raises(KlipperError, match="not below INDX tool_count"):
            backend.handle_ready()

    def test_duplicate_tool_index(self):
        unit, backend, _, tools = _make_indx()
        tools[2].tool_index = 0
        with pytest.raises(KlipperError, match="both use tool_index 0"):
            backend.handle_ready()

    def test_mixed_extruder_names(self):
        unit, backend, _, tools = _make_indx()
        tools[2].th_extruder_name = "extruder1"
        with pytest.raises(KlipperError, match="same extruder_name"):
            backend.handle_ready()

    def test_ignores_tools_from_other_units(self):
        unit, backend, _, tools = _make_indx()
        tools[2].tc_unit_obj = MagicMock()
        tools[2].tool_index = None
        backend.handle_ready()


class TestActiveTool:
    def test_empty_head(self):
        _, backend, _, _ = _make_indx(active=-1)
        backend.handle_ready()
        assert backend.active_index() == -1
        assert backend.active_tool() is None

    def test_tool_on_head(self):
        _, backend, _, tools = _make_indx(active=2)
        backend.handle_ready()
        assert backend.active_tool() is tools[2]
        assert backend.is_on_head(tools[2]) is True
        assert backend.is_on_head(tools[0]) is False

    def test_unknown_index_returns_none(self):
        _, backend, _, _ = _make_indx(tool_count=5, active=4)
        backend.handle_ready()
        assert backend.active_tool() is None

    def test_garbage_value_is_empty(self):
        _, backend, save_vars, _ = _make_indx()
        backend.handle_ready()
        save_vars.allVariables["active_tool"] = "bogus"
        assert backend.active_index() == -1

    def test_before_ready_is_empty(self):
        _, backend, _, tools = _make_indx(active=1)
        assert backend.active_index() == -1
        assert backend.is_on_head(tools[1]) is False

    def test_tool_without_index_never_on_head(self):
        _, backend, _, tools = _make_indx(active=0)
        backend.handle_ready()
        tools[0].tool_index = None
        assert backend.is_on_head(tools[0]) is False


class TestSelect:
    def test_select_runs_change_with_temp(self):
        unit, backend, save_vars, tools = _make_indx()
        backend.handle_ready()
        _simulate_indx(unit, save_vars)
        assert backend.select(tools[1], 215) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with(
            "INDX_CHANGE_TOOL TOOL=1 TEMP=215.0")

    def test_select_without_temp(self):
        unit, backend, save_vars, tools = _make_indx()
        backend.handle_ready()
        _simulate_indx(unit, save_vars)
        assert backend.select(tools[2]) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with(
            "INDX_CHANGE_TOOL TOOL=2")

    def test_select_mismatch_reports_error(self):
        unit, backend, save_vars, tools = _make_indx()
        backend.handle_ready()
        _simulate_indx(unit, save_vars, result=-1)
        assert backend.select(tools[1]) is False
        unit.afc.error.AFC_error.assert_called_once()
        assert "INDX reports empty head" in unit.afc.error.AFC_error.call_args.args[0]

    def test_select_macro_error_reports_and_returns_false(self):
        unit, backend, _, tools = _make_indx()
        backend.handle_ready()
        unit.afc.gcode.run_script_from_command = MagicMock(side_effect=Exception("seat fail"))
        unit.afc.function.in_print.return_value = True
        assert backend.select(tools[0]) is False
        msg = unit.afc.error.AFC_error.call_args.args[0]
        assert "seat fail" in msg
        assert unit.afc.error.AFC_error.call_args.kwargs["pause"] is True
        assert backend.afc_change_active is False

    def test_hook_suppressed_during_select(self):
        unit, backend, save_vars, tools = _make_indx()
        backend.handle_ready()
        seen = {}

        def _run(cmd):
            save_vars.allVariables["active_tool"] = 1
            seen["active"] = backend.afc_change_active
            backend.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={"TOOL": 1}))
        unit.afc.gcode.run_script_from_command = MagicMock(side_effect=_run)
        backend.select(tools[1])
        assert seen["active"] is True
        unit.afc.function.handle_activate_extruder.assert_not_called()


class TestUnselect:
    def test_unselect_parks(self):
        unit, backend, save_vars, tools = _make_indx(active=1)
        backend.handle_ready()
        _simulate_indx(unit, save_vars)
        assert backend.unselect(tools[1]) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PARK_TOOL")

    def test_unselect_still_on_head(self):
        unit, backend, save_vars, tools = _make_indx(active=1)
        backend.handle_ready()
        _simulate_indx(unit, save_vars, result=1)
        assert backend.unselect(tools[1]) is False
        assert "expected empty head" in unit.afc.error.AFC_error.call_args.args[0]


class TestToolMacros:
    def test_active_flag_follows_head(self):
        unit, backend, save_vars, tools = _make_indx(active=1)
        backend.handle_ready()
        macros = [unit.printer._objects[f"gcode_macro T{i}"] for i in range(3)]
        assert [m.variables["active"] for m in macros] == [False, True, False]
        _simulate_indx(unit, save_vars)
        backend.select(tools[2])
        assert [m.variables["active"] for m in macros] == [False, False, True]
        backend.unselect(tools[2])
        assert [m.variables["active"] for m in macros] == [False, False, False]

    def test_follows_loaded_lane_map(self):
        unit, backend, _, tools = _make_indx(active=0)
        lane = MagicMock()
        lane.name = "lane5"
        lane.extruder_obj = tools[0]
        lane.map = ["T7"]
        unit.afc.lanes["lane5"] = lane
        tools[0].lane_loaded = "lane5"
        unit.printer._objects["gcode_macro T7"] = FakeMacro(active=False)
        backend.handle_ready()
        assert unit.printer._objects["gcode_macro T7"].variables["active"] is True
        assert unit.printer._objects["gcode_macro T0"].variables["active"] is False

    def test_macros_without_active_untouched(self):
        unit, backend, _, _ = _make_indx(active=0)
        unit.printer._objects["gcode_macro T0"] = FakeMacro(other=1)
        backend.handle_ready()
        assert unit.printer._objects["gcode_macro T0"].variables == {"other": 1}

    def test_missing_macros_ok(self):
        _, backend, _, _ = _make_indx(active=0, macros=False)
        backend.handle_ready()

    def test_lanes_of_other_units_untouched(self):
        unit, backend, _, _ = _make_indx(active=0)
        other = MagicMock()
        other.extruder_obj = MagicMock()
        other.map = ["T9"]
        unit.afc.lanes["other"] = other
        unit.printer._objects["gcode_macro T9"] = FakeMacro(active=True)
        backend.handle_ready()
        assert unit.printer._objects["gcode_macro T9"].variables["active"] is True


class TestHook:
    def test_hook_before_prep_only_updates_macros(self):
        unit, backend, save_vars, _ = _make_indx(active=-1)
        backend.handle_ready()
        save_vars.allVariables["active_tool"] = 1
        backend.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={"TOOL": 1}))
        unit.afc.function.handle_activate_extruder.assert_not_called()
        assert unit.printer._objects["gcode_macro T1"].variables["active"] is True

    def test_hook_syncs_afc(self):
        unit, backend, save_vars, _ = _make_indx(active=-1)
        unit.afc.prep_done = True
        backend.handle_ready()
        save_vars.allVariables["active_tool"] = 0
        unit.afc.gcode._commands["AFC_INDX_TOOL_CHANGED"](MockGCodeCommand(params={"TOOL": 0}))
        unit.afc.function.handle_activate_extruder.assert_called_once()
        assert unit.printer._objects["gcode_macro T0"].variables["active"] is True


class TestToolSwapWithIndx:
    def _unit(self):
        unit, backend, save_vars, tools = _make_indx()
        unit.afc.gcode_move.base_position = [1.0, 2.0, 3.0, 0.0]
        unit.afc.gcode_move.homing_position = [0.0, 0.0, 0.0, 0.0]
        unit.afc.last_gcode_position = [10.0, 20.0, 30.0, 0.0]
        unit.afc.afcDeltaTime = MagicMock()
        unit.afc.afc_stats = MagicMock()
        unit.function.get_current_extruder_obj.return_value = None
        backend.handle_ready()
        return unit, backend, save_vars, tools

    def test_successful_swap_returns_true(self):
        unit, backend, save_vars, tools = self._unit()
        _simulate_indx(unit, save_vars)
        assert unit.tool_swap(tools[1].tc_lane, target_temp=230.0) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with(
            "INDX_CHANGE_TOOL TOOL=1 TEMP=230.0")

    def test_failed_swap_returns_false_and_restores_position(self):
        unit, backend, save_vars, tools = self._unit()
        _simulate_indx(unit, save_vars, result=-1)
        assert unit.tool_swap(tools[1].tc_lane) is False
        assert unit.afc.last_gcode_position[:3] == [10.0, 20.0, 30.0]
        assert unit.afc.current_state == State.IDLE
        tools[1].tc_lane.activate_toolhead_extruder.assert_not_called()

    def test_unselect_failure_stops_early(self):
        unit, backend, save_vars, tools = self._unit()
        save_vars.allVariables["active_tool"] = 1
        unit.function.get_current_extruder_obj.return_value = tools[1]
        unit.afc.function.get_current_extruder_obj.return_value = tools[1]
        _simulate_indx(unit, save_vars, result=1)
        unit.cmd_AFC_UNSELECT_TOOL(MockGCodeCommand())
        unit.afc.function.handle_activate_extruder.assert_not_called()
        assert unit.afc.current_state == State.IDLE

