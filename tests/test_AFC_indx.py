# Unit tests for extras/AFC_indx.py (AfcIndx)

from __future__ import annotations

from configparser import Error as KlipperError
from unittest.mock import MagicMock
import pytest

from extras.AFC_indx import AfcIndx
from extras.AFC_Toolchanger import AfcToolchanger
from tests.conftest import MockGCodeCommand
from tests.test_AFC_Toolchanger import _make_config_for_init


class FakeSaveVariables:
    """Stand-in for klipper's save_variables object."""

    def __init__(self, active_tool=-1):
        self.allVariables = {"active_tool": active_tool}

    def get_status(self, eventtime):
        return {"variables": self.allVariables}


class FakeMacro:
    """Stand-in for a klipper gcode_macro object."""

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


def _make_unit():
    """Real AfcIndx built through its __init__."""
    return AfcIndx(_make_config_for_init(values={"backend": "indx"}))


def _make_indx(tool_count=3, n_tools=3, active=-1, macros=True, ready=True):
    """Real AfcIndx with INDX objects registered with the printer."""
    unit = _make_unit()
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
    if ready:
        unit.handle_ready()
    afc.logger.messages.clear()
    return unit, save_vars, tools


def _macro(unit, name):
    return unit.printer._objects[f"gcode_macro {name}"]


def _active_flags(unit, n=3):
    return [_macro(unit, f"T{i}").variables["active"] for i in range(n)]


def _run_sets_active(unit, save_vars, result=None):
    """run_script_from_command acting like the INDX macros: INDX_CHANGE_TOOL sets active_tool
    to TOOL (or `result`), PARK_TOOL sets -1 (or `result`)."""
    def _run(cmd):
        if cmd.startswith("INDX_CHANGE_TOOL"):
            tool = int(cmd.split("TOOL=")[1].split()[0])
            save_vars.allVariables["active_tool"] = tool if result is None else result
        elif cmd == "PARK_TOOL":
            save_vars.allVariables["active_tool"] = -1 if result is None else result
    unit.afc.gcode.run_script_from_command = MagicMock(side_effect=_run)


# ── __init__ ─────────────────────────────────────────────────────────────────

class TestAfcIndxInit:
    def test_class_flags(self):
        assert issubclass(AfcIndx, AfcToolchanger)
        assert AfcIndx.shared_extruder is True
        assert AfcIndx.heats_on_pickup is True

    def test_initial_state(self):
        unit = _make_unit()
        assert unit.type == "Toolchanger"
        assert unit.save_vars_obj is None
        assert unit.positions_macro is None
        assert unit.afc_change_active is False

    def test_registers_hook_commands(self):
        unit = _make_unit()
        commands = unit.afc.gcode._commands
        assert commands["AFC_INDX_TOOL_CHANGED"] == unit.cmd_AFC_INDX_TOOL_CHANGED
        assert commands["AFC_INDX_PRE_LATCH"] == unit.cmd_AFC_INDX_PRE_LATCH


# ── tools ────────────────────────────────────────────────────────────────────

class TestAfcIndxTools:
    def test_returns_only_tools_of_this_unit(self):
        unit, _, tools = _make_indx()
        other = MagicMock()
        other.tc_unit_obj = MagicMock()
        unit.afc.tools["other"] = other
        assert unit.tools() == tools

    def test_empty_when_no_tools(self):
        unit, _, _ = _make_indx(n_tools=0)
        assert unit.tools() == []


# ── handle_ready ─────────────────────────────────────────────────────────────

class TestAfcIndxHandleReady:
    def test_resolves_objects(self):
        unit, save_vars, _ = _make_indx()
        assert unit.save_vars_obj is save_vars
        assert unit.positions_macro is _macro(unit, "TOOL_POSITIONS")

    def test_sorts_lanes_through_toolchanger(self):
        unit, _, _ = _make_indx(ready=False)
        unit.lanes = {"lane10": "b", "lane2": "a"}
        unit.handle_ready()
        assert list(unit.lanes) == ["lane2", "lane10"]

    def test_updates_tool_macros(self):
        unit, _, _ = _make_indx(active=1, ready=False)
        unit.handle_ready()
        assert _active_flags(unit) == [False, True, False]

    def test_missing_save_variables(self):
        unit, _, _ = _make_indx(ready=False)
        del unit.printer._objects["save_variables"]
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == ("[AFC_Toolchanger my_tc] backend: indx requires a "
                                  "[save_variables] section, INDX stores the tool on the "
                                  "head there.")
        assert unit.positions_macro is None

    def test_missing_indx_macros(self):
        unit, _, _ = _make_indx(ready=False)
        del unit.printer._objects["gcode_macro TOOL_POSITIONS"]
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == ("[AFC_Toolchanger my_tc] backend: indx requires the INDX "
                                  "macros. [gcode_macro TOOL_POSITIONS] was not found, make "
                                  "sure the AFC INDX macro files are included in your config.")

    def test_missing_tool_index(self):
        unit, _, tools = _make_indx(ready=False)
        tools[1].tool_index = None
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == "[AFC_extruder T1] tool_index is required when using INDX."

    def test_tool_index_at_tool_count(self):
        unit, _, tools = _make_indx(tool_count=2, n_tools=2, ready=False)
        tools[1].tool_index = 2
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == ("[AFC_extruder T1] tool_index 2 is not below INDX tool_count "
                                  "(2) set in indx.cfg.")

    def test_tool_index_below_tool_count_accepted(self):
        unit, _, tools = _make_indx(tool_count=2, n_tools=2, ready=False)
        tools[1].tool_index = 1
        unit.handle_ready()
        assert unit.save_vars_obj is not None

    def test_missing_tool_count_defaults_to_zero(self):
        unit, _, _ = _make_indx(ready=False)
        del _macro(unit, "TOOL_POSITIONS").variables["tool_count"]
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == ("[AFC_extruder T0] tool_index 0 is not below INDX tool_count "
                                  "(0) set in indx.cfg.")

    def test_duplicate_tool_index(self):
        unit, _, tools = _make_indx(ready=False)
        tools[2].tool_index = 0
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == "[AFC_extruder T2] and [AFC_extruder T0] both use tool_index 0."

    def test_mixed_extruder_names(self):
        unit, _, tools = _make_indx(ready=False)
        tools[2].th_extruder_name = "extruder1"
        with pytest.raises(KlipperError) as exc:
            unit.handle_ready()
        assert str(exc.value) == ("[AFC_Toolchanger my_tc] INDX tools must all use the same "
                                  "extruder_name, found: extruder, extruder1")

    def test_tools_of_other_units_ignored(self):
        unit, _, tools = _make_indx(ready=False)
        tools[2].tc_unit_obj = MagicMock()
        tools[2].tool_index = None
        unit.handle_ready()
        assert unit.save_vars_obj is not None


# ── active_index ─────────────────────────────────────────────────────────────

class TestAfcIndxActiveIndex:
    def test_before_ready(self):
        unit, _, _ = _make_indx(active=1, ready=False)
        assert unit.active_index() == -1

    def test_reads_active_tool(self):
        unit, _, _ = _make_indx(active=2)
        assert unit.active_index() == 2

    def test_string_value(self):
        unit, save_vars, _ = _make_indx()
        save_vars.allVariables["active_tool"] = "1"
        assert unit.active_index() == 1

    def test_missing_variable(self):
        unit, save_vars, _ = _make_indx()
        del save_vars.allVariables["active_tool"]
        assert unit.active_index() == -1

    def test_value_error(self):
        unit, save_vars, _ = _make_indx()
        save_vars.allVariables["active_tool"] = "bogus"
        assert unit.active_index() == -1

    def test_type_error(self):
        unit, save_vars, _ = _make_indx()
        save_vars.allVariables["active_tool"] = None
        assert unit.active_index() == -1


# ── active_tool ──────────────────────────────────────────────────────────────

class TestAfcIndxActiveTool:
    def test_empty_head(self):
        unit, _, _ = _make_indx(active=-1)
        assert unit.active_tool() is None

    def test_tool_on_head(self):
        unit, _, tools = _make_indx(active=2)
        assert unit.active_tool() is tools[2]

    def test_index_not_configured_in_afc(self):
        unit, _, _ = _make_indx(tool_count=5, active=4)
        assert unit.active_tool() is None


# ── is_on_head ───────────────────────────────────────────────────────────────

class TestAfcIndxIsOnHead:
    def test_true_when_index_matches(self):
        unit, _, tools = _make_indx(active=1)
        assert unit.is_on_head(tools[1]) is True

    def test_false_when_index_differs(self):
        unit, _, tools = _make_indx(active=1)
        assert unit.is_on_head(tools[0]) is False

    def test_false_when_tool_index_none(self):
        """tool_index None alone makes it False, even with the head empty (-1)."""
        unit, _, tools = _make_indx(active=-1)
        tools[0].tool_index = None
        assert unit.is_on_head(tools[0]) is False


# ── no_heat_indexes ──────────────────────────────────────────────────────────

class TestAfcIndxNoHeatIndexes:
    def test_before_ready(self):
        unit, _, _ = _make_indx(ready=False)
        assert unit.no_heat_indexes() == []

    def test_converts_to_int(self):
        unit, _, _ = _make_indx()
        _macro(unit, "TOOL_POSITIONS").variables["no_heat_tools"] = ["2", 1]
        assert unit.no_heat_indexes() == [2, 1]

    def test_missing_variable(self):
        unit, _, _ = _make_indx()
        del _macro(unit, "TOOL_POSITIONS").variables["no_heat_tools"]
        assert unit.no_heat_indexes() == []


# ── filter_heat ──────────────────────────────────────────────────────────────

class TestAfcIndxFilterHeat:
    def test_t_for_parked_tool_ignored(self):
        unit, _, tools = _make_indx(active=0)
        assert unit.filter_heat(tools[1], 220.0, "M104") is None
        assert unit.afc.logger.messages == []

    def test_t_for_tool_on_head_passes(self):
        unit, _, tools = _make_indx(active=1)
        assert unit.filter_heat(tools[1], 220.0, "M104") == 220.0
        assert unit.afc.logger.messages == []

    def test_no_t_skips_on_head_check(self):
        unit, _, _ = _make_indx(active=1)
        assert unit.filter_heat(None, 220.0, "M104") == 220.0

    def test_empty_head_heat_request_skipped(self):
        unit, _, _ = _make_indx(active=-1)
        assert unit.filter_heat(None, 220.0, "M109") is None
        assert unit.afc.logger.messages == [
            ("info", "M109 S220: no tool on the head, not heating (INDX heats each tool at "
                     "pickup)")]

    def test_empty_head_zero_passes(self):
        unit, _, _ = _make_indx(active=-1)
        assert unit.filter_heat(None, 0.0, "M104") == 0.0
        assert unit.afc.logger.messages == []

    def test_no_heat_tool_held_at_zero(self):
        unit, _, _ = _make_indx(active=2)
        _macro(unit, "TOOL_POSITIONS").variables["no_heat_tools"] = [2]
        assert unit.filter_heat(None, 220.0, "M104") == 0.0
        assert unit.afc.logger.messages == [
            ("info", "M104 S220: T2 has no hotend (no_heat_tools), not heating")]

    def test_no_heat_tool_zero_request_passes(self):
        """temp > 0 alone gates the no_heat rule: S0 on a no_heat tool passes as is."""
        unit, _, _ = _make_indx(active=2)
        _macro(unit, "TOOL_POSITIONS").variables["no_heat_tools"] = [2]
        assert unit.filter_heat(None, 0.0, "M104") == 0.0
        assert unit.afc.logger.messages == []

    def test_other_tool_in_no_heat_does_not_apply(self):
        """Tool on the head not in no_heat_tools alone keeps the requested temp."""
        unit, _, _ = _make_indx(active=1)
        _macro(unit, "TOOL_POSITIONS").variables["no_heat_tools"] = [2]
        assert unit.filter_heat(None, 220.0, "M104") == 220.0
        assert unit.afc.logger.messages == []


# ── select ───────────────────────────────────────────────────────────────────

class TestAfcIndxSelect:
    def test_with_temp(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars)
        assert unit.select_tool(tools[1], 215) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with(
            "INDX_CHANGE_TOOL TOOL=1 TEMP=215.0")

    def test_without_temp(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars)
        assert unit.select_tool(tools[2]) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with(
            "INDX_CHANGE_TOOL TOOL=2")

    def test_failure_result_returned(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars, result=-1)
        assert unit.select_tool(tools[1]) is False


# ── unselect ─────────────────────────────────────────────────────────────────

class TestAfcIndxUnselect:
    def test_parks(self):
        unit, save_vars, tools = _make_indx(active=1)
        _run_sets_active(unit, save_vars)
        assert unit.unselect_tool(tools[1]) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PARK_TOOL")

    def test_failure_result_returned(self):
        unit, save_vars, tools = _make_indx(active=1)
        _run_sets_active(unit, save_vars, result=1)
        assert unit.unselect_tool(None) is False


# ── _run_change ──────────────────────────────────────────────────────────────

class TestAfcIndxRunChange:
    def test_success_picks_up(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars)
        assert unit._run_change("INDX_CHANGE_TOOL TOOL=1", tools[1]) is True
        unit.afc.error.AFC_error.assert_not_called()
        assert unit.afc_change_active is False

    def test_success_parks(self):
        unit, save_vars, _ = _make_indx(active=1)
        _run_sets_active(unit, save_vars)
        assert unit._run_change("PARK_TOOL", None) is True
        unit.afc.error.AFC_error.assert_not_called()

    def test_flag_set_while_running(self):
        unit, _, tools = _make_indx()
        seen = []
        unit.afc.gcode.run_script_from_command = MagicMock(
            side_effect=lambda cmd: seen.append(unit.afc_change_active))
        unit._run_change("PARK_TOOL", None)
        assert seen == [True]
        assert unit.afc_change_active is False

    def test_macros_updated_after_change(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars)
        unit._run_change("INDX_CHANGE_TOOL TOOL=2", tools[2])
        assert _active_flags(unit) == [False, False, True]

    @pytest.mark.parametrize("in_print", [True, False])
    def test_macro_error_reported(self, in_print):
        unit, _, tools = _make_indx(active=0)
        unit.afc.gcode.run_script_from_command = MagicMock(side_effect=Exception("seat fail"))
        unit.afc.function.in_print.return_value = in_print
        assert unit._run_change("INDX_CHANGE_TOOL TOOL=1", tools[1]) is False
        unit.afc.error.AFC_error.assert_called_once_with("INDX tool change failed: seat fail",
                                                         pause=in_print)
        assert unit.afc_change_active is False
        assert _active_flags(unit) == [True, False, False]

    def test_wrong_tool_after_pickup(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars, result=2)
        unit.afc.function.in_print.return_value = False
        assert unit._run_change("INDX_CHANGE_TOOL TOOL=1", tools[1]) is False
        unit.afc.error.AFC_error.assert_called_once_with(
            "INDX reports T2 after 'INDX_CHANGE_TOOL TOOL=1', expected T1.", pause=False)

    def test_empty_head_after_pickup(self):
        unit, save_vars, tools = _make_indx()
        _run_sets_active(unit, save_vars, result=-1)
        unit.afc.function.in_print.return_value = True
        assert unit._run_change("INDX_CHANGE_TOOL TOOL=1", tools[1]) is False
        unit.afc.error.AFC_error.assert_called_once_with(
            "INDX reports empty head after 'INDX_CHANGE_TOOL TOOL=1', expected T1.", pause=True)

    def test_tool_still_on_head_after_park(self):
        unit, save_vars, _ = _make_indx(active=1)
        _run_sets_active(unit, save_vars, result=1)
        unit.afc.function.in_print.return_value = False
        assert unit._run_change("PARK_TOOL", None) is False
        unit.afc.error.AFC_error.assert_called_once_with(
            "INDX reports T1 after 'PARK_TOOL', expected empty head.", pause=False)


# ── _tool_lane ───────────────────────────────────────────────────────────────

class TestAfcIndxToolLane:
    def test_loaded_lane(self):
        unit, _, tools = _make_indx()
        loaded = MagicMock()
        unit.afc.lanes["lane5"] = loaded
        tools[0].lane_loaded = "lane5"
        assert unit._tool_lane(tools[0]) is loaded

    def test_standalone_lane_when_nothing_loaded(self):
        unit, _, tools = _make_indx()
        assert unit._tool_lane(tools[0]) is tools[0].tc_lane

    def test_loaded_lane_wins_over_standalone(self):
        """lane_loaded set alone keeps the tc_lane fallback from applying."""
        unit, _, tools = _make_indx()
        loaded = MagicMock()
        unit.afc.lanes["lane5"] = loaded
        tools[0].lane_loaded = "lane5"
        assert unit._tool_lane(tools[0]) is not tools[0].tc_lane

    def test_no_lane(self):
        """No tc_lane alone leaves nothing to fall back on."""
        unit, _, tools = _make_indx()
        tools[0].tc_lane = None
        assert unit._tool_lane(tools[0]) is None

    def test_lane_not_registered(self):
        unit, _, tools = _make_indx()
        del unit.afc.lanes["T0"]
        assert unit._tool_lane(tools[0]) is None


# ── update_tool_macros ───────────────────────────────────────────────────────

class TestAfcIndxUpdateToolMacros:
    def test_active_follows_head(self):
        unit, save_vars, _ = _make_indx(active=1)
        save_vars.allVariables["active_tool"] = 2
        unit.update_tool_macros()
        assert _active_flags(unit) == [False, False, True]

    def test_empty_head_clears_all(self):
        unit, save_vars, _ = _make_indx(active=1)
        save_vars.allVariables["active_tool"] = -1
        unit.update_tool_macros()
        assert _active_flags(unit) == [False, False, False]

    def test_variables_replaced_not_mutated(self):
        unit, save_vars, _ = _make_indx(active=0)
        before = _macro(unit, "T1").variables
        save_vars.allVariables["active_tool"] = 1
        unit.update_tool_macros()
        assert _macro(unit, "T1").variables is not before
        assert before == {"active": False}

    def test_follows_loaded_lane_map(self):
        unit, save_vars, tools = _make_indx(active=0)
        lane = MagicMock()
        lane.extruder_obj = tools[0]
        lane.map = ["T7"]
        unit.afc.lanes["lane5"] = lane
        tools[0].lane_loaded = "lane5"
        unit.printer._objects["gcode_macro T7"] = FakeMacro(active=False)
        unit.update_tool_macros()
        assert _macro(unit, "T7").variables["active"] is True
        assert _macro(unit, "T0").variables["active"] is False

    def test_lanes_of_other_units_untouched(self):
        unit, _, _ = _make_indx(active=0)
        other = MagicMock()
        other.extruder_obj = MagicMock()
        other.map = ["T9"]
        unit.afc.lanes["other"] = other
        unit.printer._objects["gcode_macro T9"] = FakeMacro(active=True)
        unit.update_tool_macros()
        assert _macro(unit, "T9").variables["active"] is True

    def test_lane_without_map(self):
        unit, _, tools = _make_indx(active=0)
        tools[1].tc_lane.map = None
        unit.update_tool_macros()
        assert _active_flags(unit) == [True, False, False]

    def test_missing_macro_skipped(self):
        unit, _, _ = _make_indx(active=0)
        del unit.printer._objects["gcode_macro T1"]
        unit.update_tool_macros()
        assert _macro(unit, "T0").variables["active"] is True

    def test_macro_without_active_untouched(self):
        unit, _, _ = _make_indx(active=0)
        unit.printer._objects["gcode_macro T0"] = FakeMacro(other=1)
        unit.update_tool_macros()
        assert _macro(unit, "T0").variables == {"other": 1}


# ── cmd_AFC_INDX_TOOL_CHANGED ────────────────────────────────────────────────

class TestAfcIndxCmdToolChanged:
    def test_ignored_during_afc_change(self):
        unit, save_vars, _ = _make_indx(active=-1)
        unit.afc.prep_done = True
        unit.afc_change_active = True
        save_vars.allVariables["active_tool"] = 1
        unit.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={"TOOL": 1}))
        unit.afc.function.handle_activate_extruder.assert_not_called()
        assert _active_flags(unit) == [False, False, False]
        assert unit.afc.logger.messages == []

    def test_before_prep_only_updates_macros(self):
        unit, save_vars, _ = _make_indx(active=-1)
        unit.afc.prep_done = False
        save_vars.allVariables["active_tool"] = 1
        unit.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={"TOOL": 1}))
        unit.afc.function.handle_activate_extruder.assert_not_called()
        assert _active_flags(unit) == [False, True, False]
        assert unit.afc.logger.messages == []

    def test_syncs_afc_after_prep(self):
        unit, save_vars, _ = _make_indx(active=-1)
        unit.afc.prep_done = True
        save_vars.allVariables["active_tool"] = 1
        unit.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={"TOOL": 1}))
        unit.afc.function.handle_activate_extruder.assert_called_once_with()
        assert _active_flags(unit) == [False, True, False]
        assert unit.afc.logger.messages == [("debug", "INDX reported T1 on head, syncing AFC")]

    def test_tool_defaults_to_empty_head(self):
        unit, _, _ = _make_indx(active=-1)
        unit.afc.prep_done = True
        unit.cmd_AFC_INDX_TOOL_CHANGED(MockGCodeCommand(params={}))
        assert unit.afc.logger.messages == [("debug", "INDX reported T-1 on head, syncing AFC")]


# ── cmd_AFC_INDX_PRE_LATCH ───────────────────────────────────────────────────

class TestAfcIndxCmdPreLatch:
    def test_unsyncs_only_indx_lanes(self):
        unit, _, tools = _make_indx(active=0)
        other = MagicMock()
        other.extruder_obj = MagicMock()
        unit.afc.lanes["other"] = other
        unit.cmd_AFC_INDX_PRE_LATCH(MockGCodeCommand())
        for tool in tools:
            tool.tc_lane.unsync_to_extruder.assert_called_once_with()
        other.unsync_to_extruder.assert_not_called()
