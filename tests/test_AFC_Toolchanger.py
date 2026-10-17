# Unit tests for extras/AFC_Toolchanger.py

from __future__ import annotations

import sys
import importlib.util
import configparser
from unittest.mock import MagicMock
import pytest

from extras.AFC_Toolchanger import AfcToolchanger
from extras.AFC import State
from extras.AFC_lane import AFCMoveWarning
from tests.test_AFC_extruder import _make_extruder_obj


def _build_gcmd(params=None, commandline=""):
    """Build a gcmd mock backed by MockGCodeCommand, matching real Klipper's
    sentinel-based .get() semantics (raises when a param has no value and no
    default is supplied by the caller under test)."""
    from tests.conftest import MockGCodeCommand
    return MockGCodeCommand(params=params or {}, commandline=commandline)


def _make_toolchanger(values=None):
    """Build a real AfcToolchanger via its actual __init__ (mocking config/
    printer/reactor dependencies), rather than bypassing construction with
    __new__. self.afc ends up a real MockAFC (not a bare MagicMock), so
    self.afc.tools/.function/.gcode/.spool/.gcode_move/.last_gcode_position
    all come pre-wired the same way they would in production.
    """
    config = _make_config_for_init(values=values)
    return AfcToolchanger(config)

def _make_extruder_for_toolchanger(toolchanger, afc_name="e0", extruder_name="extruder"):
    extruder = _make_extruder_obj(afc_name)
    extruder.th_extruder_name = extruder_name
    extruder.tc_lane = MagicMock()
    toolchanger.afc.tools[extruder.th_extruder_name] = extruder
    return extruder


# ── system_Test ──────────────────────────────────────────────────────────────

def _make_lane_for_system_test(name="lane1", tcmd="T0"):
    lane = MagicMock()
    lane.name = name
    lane.map = [tcmd]
    lane.current_map = tcmd
    lane.map_to_string = MagicMock(return_value=tcmd)
    lane.prep_state = False
    lane.load_state = False
    lane.extruder_obj.lane_loaded = None
    lane.extruder_obj.prep_on_shuttle_check.return_value = ""
    return lane


class TestSystemTest:
    def test_returns_true(self):
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test()
        result = tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)
        assert result is True

    def test_assign_tcmd_true_calls_tcmd_assign(self):
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test()
        tool.system_Test(lane, delay=0, assignTcmd=True, enable_movement=True)
        tool.afc.function.TcmdAssign.assert_called_once_with(lane)

    def test_assign_tcmd_false_does_not_call_tcmd_assign(self):
        """Covers the `assignTcmd` falsy branch, proven independently of the
        truthy case above."""
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test()
        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)
        tool.afc.function.TcmdAssign.assert_not_called()

    def test_sends_lane_data(self):
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test()
        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)
        lane.send_lane_data.assert_called_once()

    def test_marks_prep_done(self):
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test()
        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)
        lane.set_afc_prep_done.assert_called_once()

    def test_loaded_message_when_prepped_loaded_and_lane_loaded_matches_name(self):
        """All three conditions of the combined `if` true -> LOADED message,
        including the extruder's prep_on_shuttle_check() suffix."""
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test(name="lane1")
        lane.prep_state = True
        lane.load_state = True
        lane.extruder_obj.lane_loaded = "lane1"
        lane.extruder_obj.prep_on_shuttle_check.return_value = " [on shuttle]"

        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)

        raw_msgs = [m for lvl, m in tool.logger.messages if lvl == "raw"]
        assert raw_msgs == [
            "lane1 tool cmd: T0  <span class=success--text>LOADED</span> [on shuttle]"
        ]

    def test_no_loaded_message_when_prep_state_false(self):
        """Covers the `prep_state` half of the combined condition being falsy
        on its own, with load_state and lane_loaded still satisfied."""
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test(name="lane1")
        lane.prep_state = False
        lane.load_state = True
        lane.extruder_obj.lane_loaded = "lane1"

        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)

        raw_msgs = [m for lvl, m in tool.logger.messages if lvl == "raw"]
        assert raw_msgs == ["lane1 tool cmd: T0  "]

    def test_no_loaded_message_when_load_state_false(self):
        """Covers the `load_state` half of the combined condition being falsy
        on its own, with prep_state and lane_loaded still satisfied."""
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test(name="lane1")
        lane.prep_state = True
        lane.load_state = False
        lane.extruder_obj.lane_loaded = "lane1"

        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)

        raw_msgs = [m for lvl, m in tool.logger.messages if lvl == "raw"]
        assert raw_msgs == ["lane1 tool cmd: T0  "]

    def test_no_loaded_message_when_lane_loaded_does_not_match_name(self):
        """Covers the `lane_loaded == name` half of the combined condition
        being falsy on its own, with prep_state/load_state still satisfied."""
        tool = _make_toolchanger()
        lane = _make_lane_for_system_test(name="lane1")
        lane.prep_state = True
        lane.load_state = True
        lane.extruder_obj.lane_loaded = "some_other_lane"

        tool.system_Test(lane, delay=0, assignTcmd=False, enable_movement=True)

        raw_msgs = [m for lvl, m in tool.logger.messages if lvl == "raw"]
        assert raw_msgs == ["lane1 tool cmd: T0  "]


# ── _increase_unselect ───────────────────────────────────────────────────────

class TestIncreaseUnselect:
    def test_increases_count_when_current_extruder_present(self):
        tool = _make_toolchanger()
        current_extruder = _make_extruder_obj("extruder0")
        tool.afc.function.get_current_extruder_obj.return_value = current_extruder

        tool._increase_unselect()

        current_extruder.estats.tool_unselected.increase_count.assert_called_once()

    def test_does_not_raise_when_no_current_extruder(self):
        """Covers the `current_extruder` falsy branch, proven independently
        of the truthy case above."""
        tool = _make_toolchanger()
        tool.afc.function.get_current_extruder_obj.return_value = None

        # Must not raise despite there being no current extruder.
        tool._increase_unselect()


# ── move_to_hub / move_to_load / load_then_home ───────────────────────────────
# These are static overrides of AFC_unit's move interface for toolchangers,
# which don't actually move a stepper -- the toolchanger performs moves via
# tool_swap()/SELECT_TOOL instead. They always report success with 0 distance.

class TestMoveToHub:
    def test_returns_success_zero_distance_no_warning(self):
        tool = _make_toolchanger()
        lane = MagicMock()
        result = tool.move_to_hub(lane, dist=50.0, dir=MagicMock())
        assert result == (True, 0, AFCMoveWarning.NONE)


class TestMoveToLoad:
    def test_returns_success_zero_distance_no_warning(self):
        tool = _make_toolchanger()
        lane = MagicMock()
        result = tool.move_to_load(lane, dist=50.0, dir=MagicMock())
        assert result == (True, 0, AFCMoveWarning.NONE)


class TestLoadThenHome:
    def test_returns_success_zero_distance_no_warning(self):
        tool = _make_toolchanger()
        lane = MagicMock()
        result = tool.load_then_home(lane, distance=50.0, assist_active=MagicMock(),
                                     endstop=MagicMock())
        assert result == (True, 0, AFCMoveWarning.NONE)


# ── __init__ ─────────────────────────────────────────────────────────────────

def _make_config_for_init(name="AFC_Toolchanger my_tc", values=None):
    from tests.conftest import MockConfig, MockPrinter, MockAFC
    afc = MockAFC()
    printer = MockPrinter(afc=afc)
    return MockConfig(name=name, printer=printer, values=values)


class TestAfcToolchangerInit:
    def test_type_defaults_to_toolchanger(self):
        config = _make_config_for_init()
        tool = AfcToolchanger(config)
        assert tool.type == "Toolchanger"

    def test_type_uses_config_value_when_provided(self):
        config = _make_config_for_init(values={"type": "CustomToolchanger"})
        tool = AfcToolchanger(config)
        assert tool.type == "CustomToolchanger"

    def test_registers_select_tool_command(self):
        config = _make_config_for_init()
        tool = AfcToolchanger(config)
        calls = tool.functions.register_commands.call_args_list
        names = [c.args[1] for c in calls]
        assert "AFC_SELECT_TOOL" in names

    def test_registers_unselect_tool_command(self):
        config = _make_config_for_init()
        tool = AfcToolchanger(config)
        calls = tool.functions.register_commands.call_args_list
        names = [c.args[1] for c in calls]
        assert "AFC_UNSELECT_TOOL" in names

    def test_registers_set_toolhead_led_command(self):
        config = _make_config_for_init()
        tool = AfcToolchanger(config)
        calls = tool.functions.register_commands.call_args_list
        names = [c.args[1] for c in calls]
        assert "AFC_SET_TOOLHEAD_LED" in names

    def test_class_flags(self):
        assert AfcToolchanger.shared_extruder is False
        assert AfcToolchanger.heats_on_pickup is False


# ── load_config_prefix / load_config ────────────────────────────────────────

class TestLoadConfigPrefix:
    def test_returns_afc_toolchanger_instance(self):
        from extras.AFC_Toolchanger import load_config_prefix
        config = _make_config_for_init()
        instance = load_config_prefix(config)
        assert type(instance) is AfcToolchanger

    def test_ktc_backend_returns_afc_toolchanger(self):
        from extras.AFC_Toolchanger import load_config_prefix
        instance = load_config_prefix(_make_config_for_init(values={"backend": "ktc"}))
        assert type(instance) is AfcToolchanger

    def test_indx_backend_returns_afc_indx(self):
        from extras.AFC_Toolchanger import load_config_prefix
        from extras.AFC_indx import AfcIndx
        instance = load_config_prefix(_make_config_for_init(values={"backend": "indx"}))
        assert type(instance) is AfcIndx

    def test_backend_name_case_insensitive(self):
        from extras.AFC_Toolchanger import load_config_prefix
        from extras.AFC_indx import AfcIndx
        instance = load_config_prefix(_make_config_for_init(values={"backend": "INDX"}))
        assert type(instance) is AfcIndx

    def test_invalid_backend_raises(self):
        from extras.AFC_Toolchanger import load_config_prefix
        config = _make_config_for_init(values={"backend": "nope"})
        with pytest.raises(configparser.Error) as exc:
            load_config_prefix(config)
        assert str(exc.value) == ("[AFC_Toolchanger my_tc] invalid backend 'nope', "
                                  "valid options: indx, ktc")

    def test_indx_import_failure_raises_configparser_error(self, monkeypatch):
        from extras.AFC_Toolchanger import load_config_prefix
        # A None entry in sys.modules makes the import raise ImportError
        monkeypatch.setitem(sys.modules, "extras.AFC_indx", None)
        config = _make_config_for_init(values={"backend": "indx"})
        with pytest.raises(configparser.Error) as exc_info:
            load_config_prefix(config)
        assert str(exc_info.value).startswith(
            "Error trying to import AFC_indx, please rerun install-afc.sh"
        )


class TestLoadConfig:
    def test_returns_afc_toolchanger_instance(self):
        from extras.AFC_Toolchanger import load_config
        config = _make_config_for_init()
        instance = load_config(config)
        assert isinstance(instance, AfcToolchanger)


class TestcmdAFCSelectTool:
    def test_dict_tool_name(self):
        tool = _make_toolchanger()
        tool.tool_swap = MagicMock()
        extruder = _make_extruder_for_toolchanger(tool)
        extruder1 = _make_extruder_for_toolchanger(tool, "e1", "extruder1")

        key_tool = "extruder"
        gcmd = _build_gcmd({"TOOL": key_tool})

        tool.cmd_AFC_SELECT_TOOL(gcmd)
        tool.tool_swap.assert_called_once_with(extruder.tc_lane)
    
    def test_afc_extruder_tool_name(self):
        tool = _make_toolchanger()
        tool.tool_swap = MagicMock()
        extruder = _make_extruder_for_toolchanger(tool)
        extruder1 = _make_extruder_for_toolchanger(tool, "e1", "extruder1")
        
        key_tool = "e1"
        gcmd = _build_gcmd({"TOOL": key_tool})

        tool.cmd_AFC_SELECT_TOOL(gcmd)
        tool.tool_swap.assert_called_once_with(extruder1.tc_lane)
    
    def test_invalid_tool_name(self):
        tool = _make_toolchanger()
        tool.tool_swap = MagicMock()
        extruder = _make_extruder_for_toolchanger(tool)
        extruder1 = _make_extruder_for_toolchanger(tool, "e1", "extruder1")
        
        key_tool = "e2"
        gcmd = _build_gcmd({"TOOL": key_tool})

        tool.cmd_AFC_SELECT_TOOL(gcmd)
        tool.tool_swap.assert_not_called()
        error_msg = [m for lvl, m in tool.logger.messages if lvl == "error"]
        assert any(f"Key:{key_tool} invalid for TOOL" in m for m in error_msg)

    def test_invalid_tc_lane(self):
        tool = _make_toolchanger()
        tool.tool_swap = MagicMock()
        extruder = _make_extruder_for_toolchanger(tool)
        extruder1 = _make_extruder_for_toolchanger(tool, "e1", "extruder1")
        extruder1.tc_lane = None

        key_tool = "e1"
        gcmd = _build_gcmd({"TOOL": key_tool})

        tool.cmd_AFC_SELECT_TOOL(gcmd)
        tool.tool_swap.assert_not_called()
        error_msg = [m for lvl, m in tool.logger.messages if lvl == "error"]
        assert any(f"Tool '{key_tool}' does not have a valid 'tc_lane' attribute." in m for m in error_msg)

    def test_invalid_tool_name_does_not_touch_status(self):
        """When the TOOL key doesn't resolve to any known tool, no status
        transition happens at all (there's no tool object to set it on)."""
        tool = _make_toolchanger()
        tool.tool_swap = MagicMock()
        extruder = _make_extruder_for_toolchanger(tool)
        sentinel = object()
        extruder.status = sentinel

        gcmd = _build_gcmd({"TOOL": "unknown"})

        tool.cmd_AFC_SELECT_TOOL(gcmd)

        tool.tool_swap.assert_not_called()
        assert extruder.status is sentinel


# ── cmd_AFC_UNSELECT_TOOL ───────────────────────────────────────────────────

def _make_toolchanger_for_unselect(current_extruder=None, current_lane=None):
    tool = _make_toolchanger()
    tool.afc.function.get_current_extruder_obj.return_value = current_extruder
    tool.afc.function.get_current_lane_obj.return_value = current_lane
    return tool


class TestCmdAFCUnselectTool:
    def test_calls_increase_unselect(self):
        extruder = _make_extruder_obj()
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        extruder.estats.tool_unselected.increase_count.assert_called_once()

    def test_calls_handle_activate_extruder(self):
        extruder = _make_extruder_obj()
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        tool.afc.function.handle_activate_extruder.assert_called_once_with()

    def test_current_state_goes_tool_dock_during_unselect_then_idle(self):
        extruder = _make_extruder_obj()
        extruder.custom_unselect = None
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        captured = {}

        def _capture(*_a, **_k):
            captured["state_during_unselect"] = tool.afc.current_state

        tool.afc.gcode.run_script_from_command = MagicMock(side_effect=_capture)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        assert captured["state_during_unselect"] == State.TOOL_DOCK
        assert tool.afc.current_state == State.IDLE

    def test_extruder_status_goes_tool_dock_during_unselect_then_idle(self):
        extruder = _make_extruder_obj()
        extruder.custom_unselect = None
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        captured = {}

        def _capture(*_a, **_k):
            captured["status_during_unselect"] = extruder.status

        tool.afc.gcode.run_script_from_command = MagicMock(side_effect=_capture)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        assert captured["status_during_unselect"] == State.TOOL_DOCK
        assert extruder.status == State.IDLE

    def test_no_current_extruder_does_not_raise_and_still_idles(self):
        """Covers the `current_extruder` falsy branch: no .status assignment
        happens, but current_state still transitions normally."""
        tool = _make_toolchanger_for_unselect(current_extruder=None)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        assert tool.afc.current_state == State.IDLE

    def test_runs_custom_unselect_when_defined(self):
        extruder = _make_extruder_obj()
        extruder.custom_unselect = "MY_CUSTOM_UNSELECT"
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("MY_CUSTOM_UNSELECT")
        info_msgs = [m for lvl, m in tool.logger.messages if lvl == "info"]
        assert any("MY_CUSTOM_UNSELECT" in m for m in info_msgs)

    def test_runs_default_unselect_when_no_custom_unselect(self):
        """Covers the `current_extruder.custom_unselect` falsy half of the
        combined condition, with current_extruder itself still truthy."""
        extruder = _make_extruder_obj()
        extruder.custom_unselect = None
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")

    def test_failed_unselect_stops_with_extruder(self):
        extruder = _make_extruder_obj()
        tool = _make_toolchanger_for_unselect(current_extruder=extruder)
        tool.unselect_tool = MagicMock(return_value=False)
        tool.cmd_AFC_UNSELECT_TOOL(_build_gcmd())
        assert tool.afc.current_state == State.IDLE
        assert extruder.status == State.IDLE
        tool.afc.function.handle_activate_extruder.assert_not_called()
        tool.afc.spool.set_active_spool.assert_not_called()

    def test_failed_unselect_stops_without_extruder(self):
        tool = _make_toolchanger_for_unselect(current_extruder=None)
        tool.unselect_tool = MagicMock(return_value=False)
        tool.cmd_AFC_UNSELECT_TOOL(_build_gcmd())
        assert tool.afc.current_state == State.IDLE
        tool.afc.function.handle_activate_extruder.assert_not_called()

    def test_runs_default_unselect_when_no_current_extruder(self):
        """Covers the `current_extruder` falsy half of the combined condition."""
        tool = _make_toolchanger_for_unselect(current_extruder=None)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")

    def test_lane_tool_loaded_idle_called_when_prepped_loaded_and_tooled(self):
        lane_obj = MagicMock()
        lane_obj.prep_state = True
        lane_obj.load_state = True
        lane_obj.tool_loaded = True
        tool = _make_toolchanger_for_unselect(current_lane=lane_obj)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        lane_obj.unit_obj.lane_tool_loaded_idle.assert_called_once_with(lane_obj)
        lane_obj.unit_obj.lane_tool_unloaded.assert_not_called()
        lane_obj.extruder_obj.set_status_led.assert_not_called()

    def test_lane_tool_unloaded_called_when_prepped_loaded_not_tooled(self):
        lane_obj = MagicMock()
        lane_obj.prep_state = True
        lane_obj.load_state = True
        lane_obj.tool_loaded = False
        tool = _make_toolchanger_for_unselect(current_lane=lane_obj)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        lane_obj.unit_obj.lane_tool_unloaded.assert_called_once_with(lane_obj)
        lane_obj.unit_obj.lane_tool_loaded_idle.assert_not_called()

    def test_set_status_led_when_prep_state_false(self):
        """Covers the `prep_state` half of `prep_state and load_state` being
        falsy on its own, with load_state still true."""
        lane_obj = MagicMock()
        lane_obj.prep_state = False
        lane_obj.load_state = True
        tool = _make_toolchanger_for_unselect(current_lane=lane_obj)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        lane_obj.extruder_obj.set_status_led.assert_called_once_with(lane_obj.led_tool_unloaded)
        lane_obj.unit_obj.lane_tool_loaded_idle.assert_not_called()
        lane_obj.unit_obj.lane_tool_unloaded.assert_not_called()

    def test_set_status_led_when_load_state_false(self):
        """Covers the `load_state` half of `prep_state and load_state` being
        falsy on its own, with prep_state still true."""
        lane_obj = MagicMock()
        lane_obj.prep_state = True
        lane_obj.load_state = False
        tool = _make_toolchanger_for_unselect(current_lane=lane_obj)
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        lane_obj.extruder_obj.set_status_led.assert_called_once_with(lane_obj.led_tool_unloaded)

    def test_no_lane_actions_when_no_current_lane(self):
        tool = _make_toolchanger_for_unselect(current_lane=None)
        gcmd = _build_gcmd()

        # Must not raise despite no lane_obj being present.
        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

    def test_sets_active_spool_empty(self):
        tool = _make_toolchanger_for_unselect()
        gcmd = _build_gcmd()

        tool.cmd_AFC_UNSELECT_TOOL(gcmd)

        tool.afc.spool.set_active_spool.assert_called_once_with('')


# ── tools / tool_lanes ───────────────────────────────────────────────────────

def _make_tc_tool(unit, name):
    tool = MagicMock()
    tool.name = name
    tool.tc_unit_obj = unit
    tool.tc_lane = MagicMock()
    unit.afc.tools[name] = tool
    return tool


def _make_tc_lane(unit, name, tool):
    lane = MagicMock()
    lane.name = name
    lane.extruder_obj = tool
    unit.afc.lanes[name] = lane
    return lane


class TestAfcToolchangerTools:
    def test_returns_only_tools_of_this_unit(self):
        unit = _make_toolchanger()
        tools = [_make_tc_tool(unit, "tool0"), _make_tc_tool(unit, "tool1")]
        other = _make_tc_tool(unit, "other")
        other.tc_unit_obj = MagicMock()
        assert unit.tools() == tools

    def test_empty_when_no_tools(self):
        assert _make_toolchanger().tools() == []


class TestAfcToolchangerToolLanes:
    def test_unit_lanes_on_unit_tools(self):
        unit = _make_toolchanger()
        tool = _make_tc_tool(unit, "tool0")
        lane = _make_tc_lane(unit, "lane1", tool)
        assert unit.tool_lanes() == [lane]

    def test_standalone_lanes_left_out(self):
        unit = _make_toolchanger()
        tool = _make_tc_tool(unit, "tool0")
        unit.afc.lanes["tool0"] = tool.tc_lane
        tool.tc_lane.extruder_obj = tool
        assert unit.tool_lanes() == []

    def test_lanes_on_other_tools_left_out(self):
        unit = _make_toolchanger()
        _make_tc_tool(unit, "tool0")
        _make_tc_lane(unit, "lane1", MagicMock())
        assert unit.tool_lanes() == []


# ── handle_ready / _check_hubs ───────────────────────────────────────────────

def _make_hub_lane(unit, name, tool, hub="hub0", direct=False):
    lane = _make_tc_lane(unit, name, tool)
    lane.is_direct_hub.return_value = direct
    if hub is None:
        lane.hub_obj = None
    else:
        lane.hub_obj.name = hub
    return lane


class TestAfcToolchangerHandleReady:
    def test_sorts_lanes(self):
        unit = _make_toolchanger()
        unit.lanes = {"lane10": "b", "lane2": "a"}
        unit.handle_ready()
        assert list(unit.lanes) == ["lane2", "lane10"]

    def test_checks_hubs(self):
        unit = _make_toolchanger()
        _make_hub_lane(unit, "lane0", _make_tc_tool(unit, "tool0"))
        _make_hub_lane(unit, "lane1", _make_tc_tool(unit, "tool1"))
        with pytest.raises(configparser.Error):
            unit.handle_ready()


class TestAfcToolchangerCheckHubs:
    def test_no_lanes_passes(self):
        _make_toolchanger()._check_hubs()

    def test_hub_feeding_two_unit_tools(self):
        unit = _make_toolchanger()
        _make_hub_lane(unit, "lane0", _make_tc_tool(unit, "tool0"))
        _make_hub_lane(unit, "lane1", _make_tc_tool(unit, "tool1"))
        with pytest.raises(configparser.Error) as exc:
            unit._check_hubs()
        assert str(exc.value) == ("[AFC_hub hub0] feeds lanes on tool0, tool1. A hub on a "
                                  "toolchanger can only feed one tool.")

    def test_hub_feeding_unit_and_other_tool(self):
        unit = _make_toolchanger()
        other = MagicMock()
        other.name = "extruder"
        _make_hub_lane(unit, "lane0", _make_tc_tool(unit, "tool0"))
        _make_hub_lane(unit, "lane1", other)
        with pytest.raises(configparser.Error) as exc:
            unit._check_hubs()
        assert str(exc.value) == ("[AFC_hub hub0] feeds lanes on extruder, tool0. A hub on a "
                                  "toolchanger can only feed one tool.")

    def test_hub_feeding_two_other_tools_passes(self):
        unit = _make_toolchanger()
        for i in range(2):
            other = MagicMock()
            other.name = f"extruder{i}"
            _make_hub_lane(unit, f"lane{i}", other)
        unit._check_hubs()

    def test_hub_feeding_one_tool_passes(self):
        unit = _make_toolchanger()
        tool = _make_tc_tool(unit, "tool0")
        _make_hub_lane(unit, "lane0", tool)
        _make_hub_lane(unit, "lane1", tool)
        _make_hub_lane(unit, "lane2", _make_tc_tool(unit, "tool1"), hub="hub1")
        unit._check_hubs()

    def test_direct_hub_lanes_not_grouped(self):
        unit = _make_toolchanger()
        _make_hub_lane(unit, "lane0", _make_tc_tool(unit, "tool0"), direct=True)
        _make_hub_lane(unit, "lane1", _make_tc_tool(unit, "tool1"), direct=True)
        unit._check_hubs()

    def test_lanes_without_hub_not_grouped(self):
        unit = _make_toolchanger()
        _make_hub_lane(unit, "lane0", _make_tc_tool(unit, "tool0"), hub=None)
        _make_hub_lane(unit, "lane1", _make_tc_tool(unit, "tool1"), hub=None)
        unit._check_hubs()


# ── pickup_temp ──────────────────────────────────────────────────────────────

class TestAfcToolchangerPickupTemp:
    def _make(self, loaded=None, temps=(230.0, False)):
        unit = _make_toolchanger()
        tool = _make_tc_tool(unit, "tool0")
        tool.lane_loaded = loaded
        lane = _make_tc_lane(unit, "lane0", tool)
        _make_tc_lane(unit, "lane1", tool)
        unit.afc._get_default_material_temps = MagicMock(return_value=temps)
        return unit, tool, lane

    def test_empty_tool_keeps_target_temp(self):
        unit, _, lane = self._make()
        assert unit.pickup_temp(lane, 210.0) == 210.0
        unit.afc._get_default_material_temps.assert_not_called()
        assert unit.afc.logger.messages == []

    def test_tool_holding_target_lane_keeps_target_temp(self):
        unit, _, lane = self._make(loaded="lane0")
        assert unit.pickup_temp(lane, 210.0) == 210.0
        unit.afc._get_default_material_temps.assert_not_called()

    def test_unknown_loaded_lane_keeps_target_temp(self):
        unit, _, lane = self._make(loaded="missing")
        assert unit.pickup_temp(lane, 210.0) == 210.0
        unit.afc._get_default_material_temps.assert_not_called()

    def test_tool_holding_other_lane_uses_its_temp(self):
        unit, tool, lane = self._make(loaded="lane1")
        assert unit.pickup_temp(lane, 210.0) == 230.0
        unit.afc._get_default_material_temps.assert_called_once_with(
            unit.afc.lanes["lane1"], tool.get_heater.return_value)
        assert unit.afc.logger.messages == [("debug", "Picking up tool0 at 230 to unload lane1")]

    def test_fallback_temp_keeps_target_temp(self):
        unit, _, lane = self._make(loaded="lane1", temps=(205.0, True))
        assert unit.pickup_temp(lane, None) is None
        assert unit.afc.logger.messages == []


# ── tool_swap ────────────────────────────────────────────────────────────────

def _make_toolchanger_for_tool_swap():
    # Real __init__ already wires self.function = self.afc.function (see
    # afcUnit.__init__), so both tool_swap()'s own current-extruder lookup
    # and _increase_unselect()'s resolve to the same mock without needing to
    # alias them by hand. self.afc.gcode_move is a bare MagicMock though, so
    # base_position/homing_position still need to be real (indexable) lists.
    # afcDeltaTime/afc_stats aren't part of MockAFC's defaults, so tool_swap()
    # needs them added explicitly.
    tool = _make_toolchanger()
    tool.afc.gcode_move.base_position = [0.0, 0.0, 0.0, 0.0]
    tool.afc.gcode_move.homing_position = [0.0, 0.0, 0.0, 0.0]
    tool.afc.afcDeltaTime = MagicMock()
    tool.afc.afc_stats = MagicMock()
    return tool


def _make_lane_for_tool_swap(extruder_name="extruder1"):
    lane = MagicMock()
    lane.extruder_obj = _make_extruder_obj(extruder_name)
    lane.extruder_obj.th_extruder_name = extruder_name
    lane.extruder_obj.custom_tool_swap = None
    return lane


class TestToolSwap:
    def test_heats_on_pickup_uses_pickup_temp(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.heats_on_pickup = True
        tool.pickup_temp = MagicMock(return_value=230.0)
        tool.select_tool = MagicMock(return_value=True)
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane, target_temp=210.0)

        tool.pickup_temp.assert_called_once_with(lane, 210.0)
        tool.select_tool.assert_called_once_with(lane.extruder_obj, 230.0)

    def test_own_heaters_keep_target_temp(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.pickup_temp = MagicMock(return_value=230.0)
        tool.select_tool = MagicMock(return_value=True)
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane, target_temp=210.0)

        tool.pickup_temp.assert_not_called()
        tool.select_tool.assert_called_once_with(lane.extruder_obj, 210.0)

    def test_lane_extruder_status_pickup_and_next_pickup_during_swap(self):
        """lane.extruder_obj.status is TOOL_PICKUP and next_pickup is True
        while the SELECT_TOOL command is being issued."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()
        captured = {}

        def _capture(*_a, **_k):
            captured["status"] = lane.extruder_obj.status
            captured["next_pickup"] = lane.extruder_obj.next_pickup

        tool.afc.gcode.run_script_from_command = MagicMock(side_effect=_capture)

        tool.tool_swap(lane)

        assert captured["status"] == State.TOOL_PICKUP
        assert captured["next_pickup"] is True

    def test_lane_extruder_next_pickup_and_status_reset_after_swap(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        assert lane.extruder_obj.next_pickup is False
        assert lane.extruder_obj.status == State.IDLE
        assert tool.afc.current_state == State.IDLE

    def test_current_extruder_status_tool_dock_during_swap_then_idle(self):
        """The previously-active extruder (distinct from the lane being
        swapped to) is parked at TOOL_DOCK for the duration of the swap."""
        tool = _make_toolchanger_for_tool_swap()
        current_extruder = _make_extruder_obj("extruder0")
        tool.function.get_current_extruder_obj.return_value = current_extruder
        lane = _make_lane_for_tool_swap()
        captured = {}

        def _capture(*_a, **_k):
            captured["status"] = current_extruder.status

        tool.afc.gcode.run_script_from_command = MagicMock(side_effect=_capture)

        tool.tool_swap(lane)

        assert captured["status"] == State.TOOL_DOCK
        assert current_extruder.status == State.IDLE

    def test_no_current_extruder_does_not_raise(self):
        """Covers the `current_extruder` falsy branch: no .status assignment
        happens on a None object, and the swap still completes normally."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        assert lane.extruder_obj.status == State.IDLE

    def test_increase_unselect_invoked_during_swap(self):
        """tool_swap() calls self._increase_unselect(), which bumps the
        previously-active extruder's tool_unselected count."""
        tool = _make_toolchanger_for_tool_swap()
        current_extruder = _make_extruder_obj("extruder0")
        tool.function.get_current_extruder_obj.return_value = current_extruder
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        current_extruder.estats.tool_unselected.increase_count.assert_called_once()

    def test_custom_tool_swap_used_when_defined(self):
        """Covers the `lane.extruder_obj.custom_tool_swap` truthy branch:
        the custom macro is run instead of SELECT_TOOL, and logged."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()
        lane.extruder_obj.custom_tool_swap = "MY_CUSTOM_SWAP"

        tool.tool_swap(lane)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("MY_CUSTOM_SWAP")
        info_msgs = [m for lvl, m in tool.logger.messages if lvl == "info"]
        assert any("MY_CUSTOM_SWAP" in m for m in info_msgs)

    def test_tool_index_zero_for_plain_extruder_name(self):
        """Covers the "extruder" half of the tool_index ternary: the plain
        `extruder` name maps to index 0."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap(extruder_name="extruder")

        tool.tool_swap(lane)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=0")

    def test_tool_index_parsed_from_numbered_extruder_name(self):
        """Covers the numbered-extruder half of the tool_index ternary: the
        trailing digits of the extruder name are parsed out as the index."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap(extruder_name="extruder2")

        tool.tool_swap(lane)

        tool.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=2")

    def test_average_tool_swap_time_called_when_set(self):
        """Covers the `self.afc.afc_stats.average_tool_swap_time` truthy branch."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.afc.afc_stats.average_tool_swap_time = MagicMock()
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        tool.afc.afc_stats.average_tool_swap_time.average_time.assert_called_once_with(
            tool.afc.afcDeltaTime.delta_time
        )

    def test_average_tool_swap_time_not_called_when_unset(self):
        """Covers the `self.afc.afc_stats.average_tool_swap_time` falsy branch,
        proven independently of the truthy case above."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.afc.afc_stats.average_tool_swap_time = None
        lane = _make_lane_for_tool_swap()

        # Must not raise despite average_tool_swap_time being None.
        tool.tool_swap(lane)

    def test_set_start_time_true_calls_set_start_time(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane, set_start_time=True)

        tool.afc.afcDeltaTime.set_start_time.assert_called_once()

    def test_set_start_time_false_does_not_call_set_start_time(self):
        """Covers the `set_start_time` falsy branch, proven independently of
        the truthy case above."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane, set_start_time=False)

        tool.afc.afcDeltaTime.set_start_time.assert_not_called()

    def test_activates_toolhead_extruder_and_handles_activation(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        lane.activate_toolhead_extruder.assert_called_once()
        tool.afc.function._handle_activate_extruder.assert_called_once_with(0)

    def test_waits_for_toolhead_moves(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        tool.afc.toolhead.wait_moves.assert_called_once()

    def test_logs_before_and_after_toolhead_pos_in_order(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        calls = tool.afc.function.log_toolhead_pos.call_args_list
        assert [c.args[0] for c in calls] == ["Before toolswap: ", "After toolswap: "]

    def test_tool_selected_count_increased(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        lane.extruder_obj.estats.tool_selected.increase_count.assert_called_once()

    def test_returns_true_on_success(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        lane = _make_lane_for_tool_swap()
        assert tool.tool_swap(lane) is True

    def test_target_temp_passed_to_select_tool(self):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.select_tool = MagicMock(return_value=True)
        lane = _make_lane_for_tool_swap()
        tool.tool_swap(lane, target_temp=215.0)
        tool.select_tool.assert_called_once_with(lane.extruder_obj, 215.0)

    def _failed_swap(self, current_extruder):
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = current_extruder
        tool.select_tool = MagicMock(return_value=False)
        tool.afc.last_gcode_position = [10.0, 20.0, 5.0, 100.0]
        tool.afc.gcode_move.base_position = [1.0, 2.0, 3.0, 4.0]
        tool.afc.base_position = "unchanged"
        lane = _make_lane_for_tool_swap()
        result = tool.tool_swap(lane)
        return tool, lane, result

    def test_failed_select_returns_false_and_stops(self):
        tool, lane, result = self._failed_swap(None)
        assert result is False
        assert tool.afc.current_state == State.IDLE
        assert lane.extruder_obj.status == State.IDLE
        assert lane.extruder_obj.next_pickup is False
        lane.activate_toolhead_extruder.assert_not_called()
        lane.extruder_obj.estats.tool_selected.increase_count.assert_not_called()
        assert tool.afc.base_position == "unchanged"

    def test_failed_select_restores_saved_position(self):
        """last_gcode_position had base_position subtracted before the swap, so a failed
        swap adds it back: 10-1+1, 20-2+2, 5-3+3, and E untouched."""
        tool, _, _ = self._failed_swap(None)
        assert tool.afc.last_gcode_position == [10.0, 20.0, 5.0, 100.0]

    def test_failed_select_idles_current_extruder(self):
        current = _make_extruder_obj("extruder0")
        tool, _, _ = self._failed_swap(current)
        assert current.status == State.IDLE

    def test_position_bookkeeping_after_swap(self):
        """base_position/homing_position are copied (not aliased) from
        gcode_move, and last_gcode_position round-trips back to its original
        values since it's offset by the same base_position before and after."""
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.afc.last_gcode_position = [10.0, 20.0, 5.0, 100.0]
        tool.afc.gcode_move.base_position = [1.0, 2.0, 3.0, 4.0]
        tool.afc.gcode_move.homing_position = [0.5, 0.5, 0.5, 0.5]
        lane = _make_lane_for_tool_swap()

        tool.tool_swap(lane)

        assert tool.afc.last_gcode_position == [10.0, 20.0, 5.0, 100.0]
        assert tool.afc.base_position == [1.0, 2.0, 3.0, 4.0]
        assert tool.afc.homing_position == [0.5, 0.5, 0.5, 0.5]
        assert tool.afc.base_position is not tool.afc.gcode_move.base_position
        assert tool.afc.homing_position is not tool.afc.gcode_move.homing_position


# ── cmd_AFC_SET_TOOLHEAD_LED ────────────────────────────────────────────────

def _make_gcmd_for_set_toolhead_led(mapping="T0", turn_on=1):
    return _build_gcmd({"MAP": mapping, "TURN_ON": turn_on})


class TestCmdAFCSetToolheadLed:
    def test_raises_and_logs_error_when_map_not_found(self):
        """Covers the `lane_obj is None` branch: an unknown mapping logs and
        raises gcmd.error without touching any lane's LEDs."""
        tool = _make_toolchanger()
        tool.afc.function.get_lane_by_map.return_value = None
        gcmd = _make_gcmd_for_set_toolhead_led(mapping="T9")

        with pytest.raises(Exception, match="T9 mapping not found when trying to set toolhead led"):
            tool.cmd_AFC_SET_TOOLHEAD_LED(gcmd)

        error_msgs = [m for lvl, m in tool.logger.messages if lvl == "error"]
        assert error_msgs == ["T9 mapping not found when trying to set toolhead led"]

    def test_sets_print_leds_on_found_lane(self):
        tool = _make_toolchanger()
        lane_obj = MagicMock()
        lane_obj.extruder_obj.set_print_leds.return_value = (True, "")
        tool.afc.function.get_lane_by_map.return_value = lane_obj
        gcmd = _make_gcmd_for_set_toolhead_led(mapping="T2", turn_on=1)

        tool.cmd_AFC_SET_TOOLHEAD_LED(gcmd)

        lane_obj.extruder_obj.set_print_leds.assert_called_once_with(1)

    def test_turn_on_zero_passed_through(self):
        tool = _make_toolchanger()
        lane_obj = MagicMock()
        lane_obj.extruder_obj.set_print_leds.return_value = (True, "")
        tool.afc.function.get_lane_by_map.return_value = lane_obj
        gcmd = _make_gcmd_for_set_toolhead_led(mapping="T2", turn_on=0)

        tool.cmd_AFC_SET_TOOLHEAD_LED(gcmd)

        lane_obj.extruder_obj.set_print_leds.assert_called_once_with(0)

    def test_success_does_not_raise(self):
        """Covers the `not success` falsy branch, proven independently of
        the failure case below."""
        tool = _make_toolchanger()
        lane_obj = MagicMock()
        lane_obj.extruder_obj.set_print_leds.return_value = (True, "some error we ignore")
        tool.afc.function.get_lane_by_map.return_value = lane_obj
        gcmd = _make_gcmd_for_set_toolhead_led()

        # Must not raise when set_print_leds reports success.
        tool.cmd_AFC_SET_TOOLHEAD_LED(gcmd)

    def test_raises_gcmd_error_when_set_print_leds_fails(self):
        """Covers the `not success` truthy branch."""
        tool = _make_toolchanger()
        lane_obj = MagicMock()
        lane_obj.extruder_obj.set_print_leds.return_value = (False, "led index out of range")
        tool.afc.function.get_lane_by_map.return_value = lane_obj
        gcmd = _make_gcmd_for_set_toolhead_led()

        with pytest.raises(Exception, match="led index out of range"):
            tool.cmd_AFC_SET_TOOLHEAD_LED(gcmd)


# ═════════════════════════════════════════════════════════════════════════
# Module-level import guards
# ═════════════════════════════════════════════════════════════════════════

def _exec_afc_toolchanger_with_blocked_dependency(blocked_module_name):
    """Execute a throw-away copy of extras/AFC_Toolchanger.py's module-level code
    with `blocked_module_name` forced to fail import, to exercise the file's
    top-level ``try: from X import Y / except: raise error(...)`` guards.

    This never touches the real, already-imported ``extras.AFC_Toolchanger``
    module that the rest of this test suite depends on: the copy is loaded
    under a throwaway module name and discarded afterward, whether or not it
    raises. Blocking an import via ``sys.modules[name] = None`` is a standard
    Python mechanism -- it makes any ``import``/``from ... import`` of that
    name raise ImportError immediately, without touching the module itself.

    Cleanup restores the *exact same* pre-existing module object in
    sys.modules (not just removes the block) -- simply deleting the entry
    would let it get re-imported fresh the next time anything touches it,
    producing new, distinct class objects that no longer match what other
    test files already imported and bound references to.
    """
    import extras.AFC_Toolchanger as real_module
    fresh_name = "extras.AFC_Toolchanger_import_guard_probe"
    original_blocked_module = sys.modules.get(blocked_module_name)
    sys.modules[blocked_module_name] = None
    try:
        spec = importlib.util.spec_from_file_location(fresh_name, real_module.__file__)
        fresh = importlib.util.module_from_spec(spec)
        sys.modules[fresh_name] = fresh
        try:
            spec.loader.exec_module(fresh)
        finally:
            sys.modules.pop(fresh_name, None)
    finally:
        if original_blocked_module is not None:
            sys.modules[blocked_module_name] = original_blocked_module
        else:
            sys.modules.pop(blocked_module_name, None)


class TestModuleImportGuard:
    """Covers the four module-level `try/except: raise error(...)` guards in
    AFC_Toolchanger.py, one per dependency import."""

    def test_afc_utils_import_failure_raises_configparser_error(self):
        """The very first guard imports ERROR_STR itself from AFC_utils, so
        it can't use ERROR_STR.format(...) in its own except clause."""
        with pytest.raises(configparser.Error) as exc_info:
            _exec_afc_toolchanger_with_blocked_dependency("extras.AFC_utils")
        assert str(exc_info.value).startswith(
            "Error when trying to import AFC_utils.ERROR_STR"
        )

    def test_afc_unit_import_failure_raises_configparser_error(self):
        with pytest.raises(configparser.Error) as exc_info:
            _exec_afc_toolchanger_with_blocked_dependency("extras.AFC_unit")
        assert str(exc_info.value).startswith(
            "Error trying to import AFC_unit, please rerun install-afc.sh"
        )

    def test_afc_import_failure_raises_configparser_error(self):
        with pytest.raises(configparser.Error) as exc_info:
            _exec_afc_toolchanger_with_blocked_dependency("extras.AFC")
        assert str(exc_info.value).startswith(
            "Error trying to import AFC, please rerun install-afc.sh"
        )

    def test_afc_lane_import_failure_raises_configparser_error(self):
        with pytest.raises(configparser.Error) as exc_info:
            _exec_afc_toolchanger_with_blocked_dependency("extras.AFC_lane")
        assert str(exc_info.value).startswith(
            "Error trying to import AFC_lane, please rerun install-afc.sh"
        )


def _make_ktc_extruder(name="extruder", custom_swap=None, custom_unselect=None):
    ext = _make_extruder_obj(name)
    ext.custom_tool_swap = custom_swap
    ext.custom_unselect = custom_unselect
    return ext


def _make_quiet_toolchanger():
    unit = _make_toolchanger()
    unit.afc.logger.messages.clear()
    return unit


# ── select_tool ──────────────────────────────────────────────────────────────

class TestSelectTool:
    def test_plain_extruder_is_tool_zero(self):
        unit = _make_quiet_toolchanger()
        assert unit.select_tool(_make_ktc_extruder("extruder")) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=0")
        assert unit.afc.logger.messages == []

    def test_numbered_extruder(self):
        unit = _make_quiet_toolchanger()
        assert unit.select_tool(_make_ktc_extruder("extruder3"), target_temp=220.0) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=3")
        assert unit.afc.logger.messages == []

    def test_custom_swap(self):
        unit = _make_quiet_toolchanger()
        ext = _make_ktc_extruder("extruder1", custom_swap="PICK_EXTRUDER1")
        assert unit.select_tool(ext) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PICK_EXTRUDER1")
        assert unit.afc.logger.messages == [("info", "Running custom select: PICK_EXTRUDER1")]


# ── unselect_tool ────────────────────────────────────────────────────────────

class TestUnselectTool:
    def test_custom_unselect(self):
        unit = _make_quiet_toolchanger()
        assert unit.unselect_tool(_make_ktc_extruder(custom_unselect="PARK_EXTRUDER")) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PARK_EXTRUDER")
        assert unit.afc.logger.messages == [("info", "Running custom unselect: PARK_EXTRUDER")]

    def test_extruder_without_custom_unselect(self):
        """custom_unselect unset alone falls back to UNSELECT_TOOL."""
        unit = _make_quiet_toolchanger()
        assert unit.unselect_tool(_make_ktc_extruder()) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")
        assert unit.afc.logger.messages == []

    def test_no_extruder(self):
        """No extruder alone falls back to UNSELECT_TOOL."""
        unit = _make_quiet_toolchanger()
        assert unit.unselect_tool(None) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")
        assert unit.afc.logger.messages == []


# ── is_on_head ───────────────────────────────────────────────────────────────

class TestIsOnHead:
    @pytest.mark.parametrize("detected", [True, False])
    def test_uses_extruder_detection(self, detected):
        unit = _make_quiet_toolchanger()
        ext = _make_ktc_extruder()
        ext.detect_on_shuttle.return_value = detected
        assert unit.is_on_head(ext) is detected
        ext.detect_on_shuttle.assert_called_once_with()


# ── filter_heat ──────────────────────────────────────────────────────────────

class TestFilterHeat:
    @pytest.mark.parametrize("temp", [0.0, 210.0])
    def test_returns_temp_unchanged(self, temp):
        unit = _make_quiet_toolchanger()
        assert unit.filter_heat(_make_ktc_extruder(), temp, "M104") == temp
        assert unit.filter_heat(None, temp, "M109") == temp
        assert unit.afc.logger.messages == []


# ── active_tool ──────────────────────────────────────────────────────────────

class TestActiveTool:
    def test_not_tracked(self):
        assert _make_quiet_toolchanger().active_tool() is None
