# Unit tests for the hotend changer support in AFC core (several AFC_extruder sections sharing
# one klipper extruder, e.g. INDX): AFC_extruder, AFC_functions, AFC, AFC_lane and AFC_prep.
# Objects are built through their real __init__.

from __future__ import annotations

from configparser import Error as KlipperError
from unittest.mock import MagicMock, patch
import pytest

from extras import AFC
from extras.AFC_extruder import AFCExtruder
from extras.AFC_functions import afcFunction
from extras.AFC_lane import AFCLaneState
from extras.AFC_prep import afcPrep
from tests.conftest import (MockAFC, MockConfig, MockGCodeCommand, MockLogger, MockPrinter,
                            MockReactor)
from tests.test_AFC_Toolchanger import _make_toolchanger
from tests.test_AFC_lane import _make_real_afc_lane


# ── Factories ────────────────────────────────────────────────────────────────

def _make_printer():
    """Printer holding a real afc object, shared by every tool built on it."""
    afc = AFC.load_config(MockConfig("AFC", MockPrinter()))
    printer = MockPrinter(afc=afc)
    printer._reactor = MockReactor()
    return printer, afc


def _make_tool(printer, name, values=None):
    """Real AFCExtruder on `printer`."""
    config = MockConfig(f"AFC_extruder {name}", printer, values or {})
    ext = AFCExtruder(config)
    ext.logger = MockLogger()
    return ext


def _make_indx_unit(printer, name="INDX"):
    """Real AfcToolchanger with backend: indx registered on `printer`."""
    unit = _make_toolchanger(values={"backend": "indx"})
    printer._objects[f"AFC_Toolchanger {name}"] = unit
    return unit


def _make_shared_tool(printer, name, index, unit_name="INDX"):
    tool = _make_tool(printer, name, {"extruder_name": "extruder", "tool_index": index,
                                      "toolchanger_unit": unit_name,
                                      "pin_tool_start": "virtual"})
    return tool


def _make_func(afc):
    """Real afcFunction connected to `afc`."""
    printer = MockPrinter(afc=afc)
    with patch.object(afcFunction, "register_commands"):
        func = afcFunction(MockConfig("AFC_functions", printer))
    func.afc = afc
    func.logger = MockLogger()
    return func


def _make_real_afc():
    """Real afc object with its collaborators mocked."""
    _, afc = _make_printer()
    afc.logger = MockLogger()
    afc.function = MagicMock()
    afc.error = MagicMock()
    afc.toolhead = MagicMock()
    afc.afc_stats = MagicMock()
    afc.afcDeltaTime = MagicMock()
    return afc


# ═════════════════════════════════════════════════════════════════════════
# AFC_extruder
# ═════════════════════════════════════════════════════════════════════════

class TestAFCExtruderInitToolIndex:
    def test_default_none(self):
        printer, _ = _make_printer()
        assert _make_tool(printer, "extruder").tool_index is None

    def test_read_from_config(self):
        printer, _ = _make_printer()
        assert _make_tool(printer, "tool3", {"extruder_name": "extruder",
                                              "tool_index": 3}).tool_index == 3


class TestAFCExtruderSharedExtruder:
    def test_no_unit(self):
        printer, _ = _make_printer()
        assert _make_tool(printer, "extruder").shared_extruder is False

    def test_unit_without_shared_extruder(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.tc_unit_obj = _make_toolchanger()
        assert tool.shared_extruder is False

    def test_unit_with_shared_extruder(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "tool0", {"extruder_name": "extruder"})
        tool.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        assert tool.shared_extruder is True


class TestAFCExtruderHeatsOnPickup:
    def test_no_unit(self):
        printer, _ = _make_printer()
        assert _make_tool(printer, "extruder").heats_on_pickup is False

    def test_unit_without_heats_on_pickup(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.tc_unit_obj = _make_toolchanger()
        assert tool.heats_on_pickup is False

    def test_unit_with_heats_on_pickup(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "tool0", {"extruder_name": "extruder"})
        tool.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        assert tool.heats_on_pickup is True


class TestAFCExtruderToolKey:
    def test_regular_tool_uses_extruder_name(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "e1", {"extruder_name": "extruder1"})
        assert tool.tool_key == "extruder1"

    def test_shared_tool_uses_afc_name(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "tool2", {"extruder_name": "extruder"})
        tool.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        assert tool.tool_key == "tool2"


class TestAFCExtruderRegisterTool:
    def _shared_pair(self):
        printer, afc = _make_printer()
        unit = _make_indx_unit(printer)
        t0 = _make_shared_tool(printer, "tool0", 0)
        t1 = _make_shared_tool(printer, "tool1", 1)
        t0.tc_unit_obj = t1.tc_unit_obj = unit
        return afc, t0, t1

    def test_registers_regular_tool(self):
        printer, afc = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool._register_tool()
        assert afc.tools == {"extruder": tool}

    def test_shared_tools_in_same_unit(self):
        afc, t0, t1 = self._shared_pair()
        t0._register_tool()
        t1._register_tool()
        assert afc.tools == {"tool0": t0, "tool1": t1}

    def test_registering_twice_is_allowed(self):
        printer, afc = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool._register_tool()
        tool._register_tool()
        assert afc.tools == {"extruder": tool}

    def test_duplicate_key(self):
        printer, afc = _make_printer()
        first = _make_tool(printer, "e0", {"extruder_name": "extruder"})
        second = _make_tool(printer, "e1", {"extruder_name": "extruder"})
        first._register_tool()
        with pytest.raises(KlipperError) as exc:
            second._register_tool()
        assert str(exc.value) == ("Duplicate toolhead extruder mapping 'extruder' between "
                                  "[AFC_extruder e0] and [AFC_extruder e1]. Each AFC_extruder "
                                  "section must map to a unique toolhead extruder.")
        assert afc.tools == {"extruder": first}

    def test_shared_tools_in_different_units(self):
        """Same extruder and both shared, but a different unit alone is rejected."""
        afc, t0, t1 = self._shared_pair()
        t1.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        t0._register_tool()
        with pytest.raises(KlipperError) as exc:
            t1._register_tool()
        assert str(exc.value) == ("[AFC_extruder tool0] and [AFC_extruder tool1] both map to "
                                  "'extruder'. Only tools in the same hotend changer unit "
                                  "(e.g. backend: indx) can share a toolhead extruder.")
        assert afc.tools == {"tool0": t0}

    def test_regular_tool_after_shared_tool(self):
        """Same unit object (None) but not shared alone is rejected."""
        printer, afc = _make_printer()
        shared = _make_shared_tool(printer, "tool0", 0)
        shared.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        regular = _make_tool(printer, "extruder")
        shared._register_tool()
        with pytest.raises(KlipperError) as exc:
            regular._register_tool()
        assert str(exc.value) == ("[AFC_extruder tool0] and [AFC_extruder extruder] both map to "
                                  "'extruder'. Only tools in the same hotend changer unit "
                                  "(e.g. backend: indx) can share a toolhead extruder.")

    def test_shared_tool_after_regular_tool(self):
        printer, afc = _make_printer()
        regular = _make_tool(printer, "extruder")
        shared = _make_shared_tool(printer, "tool0", 0)
        shared.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        regular._register_tool()
        with pytest.raises(KlipperError):
            shared._register_tool()
        assert afc.tools == {"extruder": regular}

    def test_other_extruder_is_not_a_conflict(self):
        """Different extruder name alone keeps the shared check from applying."""
        printer, afc = _make_printer()
        e1 = _make_tool(printer, "e1", {"extruder_name": "extruder1"})
        shared = _make_shared_tool(printer, "tool0", 0)
        shared.tc_unit_obj = _make_toolchanger(values={"backend": "indx"})
        e1._register_tool()
        shared._register_tool()
        assert afc.tools == {"extruder1": e1, "tool0": shared}


class TestAFCExtruderHandleConnect:
    def test_unit_looked_up_before_registering(self):
        printer, afc = _make_printer()
        unit = _make_indx_unit(printer)
        tool = _make_shared_tool(printer, "tool0", 0)
        tool.handle_connect()
        assert tool.tc_unit_obj is unit
        assert tool.tc_lane.unit_obj is unit
        assert afc.tools == {"tool0": tool}

    def test_missing_unit(self):
        printer, afc = _make_printer()
        tool = _make_shared_tool(printer, "tool0", 0)
        with pytest.raises(KlipperError) as exc:
            tool.handle_connect()
        assert str(exc.value) == ("[AFC_Toolchanger INDX] not found in config file for "
                                  "AFC_extruder tool0")
        assert afc.tools == {}

    def test_without_unit(self):
        printer, afc = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.handle_connect()
        assert tool.tc_unit_obj is None
        assert afc.tools == {"extruder": tool}


class TestAFCExtruderOnShuttle:
    @pytest.mark.parametrize("on_head", [True, False])
    def test_unit_backend_decides(self, on_head):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "tool0", {"extruder_name": "extruder"})
        tool.tc_unit_obj = MagicMock()
        tool.tc_unit_obj.backend.is_on_head.return_value = on_head
        assert tool.on_shuttle() is on_head
        tool.tc_unit_obj.backend.is_on_head.assert_called_once_with(tool)

    def test_without_unit_uses_detection(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.park_detector_obj = MagicMock()
        tool.park_detector_obj.get_park_detector_status.return_value = {"state": "IDLE"}
        tool.tc_unit_name = "Tools"
        assert tool.on_shuttle() is False


class TestAFCExtruderDetectOnShuttle:
    def test_single_toolhead(self):
        printer, _ = _make_printer()
        assert _make_tool(printer, "extruder").detect_on_shuttle() is True

    def test_unit_without_detection(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.tc_unit_name = "Tools"
        assert tool.detect_on_shuttle() is True

    def test_ignores_backend(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.tc_unit_obj = MagicMock()
        tool.tc_unit_obj.backend.is_on_head.return_value = False
        assert tool.detect_on_shuttle() is True
        tool.tc_unit_obj.backend.is_on_head.assert_not_called()

    @pytest.mark.parametrize("state,expected", [("ACTIVATE", True), ("IDLE", False)])
    def test_park_detector(self, state, expected):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.park_detector_obj = MagicMock()
        tool.park_detector_obj.get_park_detector_status.return_value = {"state": state}
        assert tool.detect_on_shuttle() is expected

    def test_tool_object_without_detection(self):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder")
        tool.tool_obj = MagicMock(spec=[])
        assert tool.detect_on_shuttle() is False


class TestAFCExtruderGetStatus:
    @pytest.mark.parametrize("values,expected", [({}, None), ({"tool_index": 4}, 4)])
    def test_reports_tool_index(self, values, expected):
        printer, _ = _make_printer()
        tool = _make_tool(printer, "extruder", values)
        tool.toolhead_extruder = MagicMock()
        tool.estats = MagicMock()
        assert tool.get_status()["tool_index"] == expected


# ═════════════════════════════════════════════════════════════════════════
# AFC_functions
# ═════════════════════════════════════════════════════════════════════════

def _tool_mock(name, extruder="extruder", shared=False, unit=None, on_shuttle=True):
    tool = MagicMock()
    tool.name = name
    tool.tool_key = name if shared else extruder
    tool.th_extruder_name = extruder
    tool.shared_extruder = shared
    tool.tc_unit_obj = unit
    tool.on_shuttle.return_value = on_shuttle
    return tool


class TestAfcFunctionGetCurrentExtruder:
    def _func(self, tools, active_extruder="extruder"):
        afc = _make_real_afc()
        afc.tools = tools
        afc.toolhead.get_extruder.return_value.name = active_extruder
        return _make_func(afc)

    def test_regular_tool_on_shuttle(self):
        func = self._func({"extruder": _tool_mock("extruder")})
        assert func.get_current_extruder() == "extruder"

    def test_regular_tool_off_shuttle(self):
        func = self._func({"extruder": _tool_mock("extruder", on_shuttle=False)})
        assert func.get_current_extruder() is None

    def test_shared_tool_on_head(self):
        unit = MagicMock()
        t0 = _tool_mock("tool0", shared=True, unit=unit)
        t1 = _tool_mock("tool1", shared=True, unit=unit)
        unit.backend.active_tool.return_value = t1
        func = self._func({"tool0": t0, "tool1": t1})
        assert func.get_current_extruder() == "tool1"

    def test_shared_tool_empty_head(self):
        unit = MagicMock()
        unit.backend.active_tool.return_value = None
        func = self._func({"tool0": _tool_mock("tool0", shared=True, unit=unit)})
        assert func.get_current_extruder() is None

    def test_other_extruder_not_matched(self):
        """Extruder name mismatch alone skips the shared lookup."""
        unit = MagicMock()
        func = self._func({"tool0": _tool_mock("tool0", shared=True, unit=unit)},
                          active_extruder="extruder5")
        assert func.get_current_extruder() is None
        unit.backend.active_tool.assert_not_called()

    def test_not_shared_not_matched(self):
        """Not shared alone skips the shared lookup."""
        unit = MagicMock()
        func = self._func({"e1": _tool_mock("e1", shared=False, unit=unit)})
        assert func.get_current_extruder() is None
        unit.backend.active_tool.assert_not_called()

    def test_shared_without_unit_not_matched(self):
        """No unit object alone skips the shared lookup."""
        func = self._func({"tool0": _tool_mock("tool0", shared=True, unit=None)})
        assert func.get_current_extruder() is None


class TestAfcFunctionGetSharedBackend:
    def _func(self, tools):
        afc = _make_real_afc()
        afc.tools = tools
        return _make_func(afc)

    def test_returns_backend(self):
        unit = MagicMock()
        func = self._func({"e1": _tool_mock("e1"),
                           "tool0": _tool_mock("tool0", shared=True, unit=unit)})
        assert func.get_shared_backend() is unit.backend

    def test_none_without_shared_tools(self):
        func = self._func({"e1": _tool_mock("e1", unit=MagicMock())})
        assert func.get_shared_backend() is None

    def test_shared_without_unit_skipped(self):
        """No unit object alone keeps a shared tool from providing a backend."""
        func = self._func({"tool0": _tool_mock("tool0", shared=True, unit=None)})
        assert func.get_shared_backend() is None


class TestAfcFunctionGetTool:
    def _func(self):
        afc = _make_real_afc()
        self.e1 = _tool_mock("e1", extruder="extruder1")
        afc.tools = {"extruder1": self.e1}
        return _make_func(afc)

    def test_by_key(self):
        assert self._func().get_tool("extruder1") is self.e1

    def test_by_afc_name(self):
        assert self._func().get_tool("e1") is self.e1

    def test_not_found(self):
        assert self._func().get_tool("nope") is None


# ═════════════════════════════════════════════════════════════════════════
# AFC
# ═════════════════════════════════════════════════════════════════════════

def _change_tool_afc(heats_on_pickup=True):
    """Real afc set up to drive CHANGE_TOOL from lane1 (tool0) to lane2 (tool1)."""
    afc = _make_real_afc()
    for name in ("TOOL_LOAD", "TOOL_UNLOAD", "LANE_UNLOAD", "save_pos", "restore_pos",
                 "_heat_next_extruder", "_cooldown_last_extruder",
                 "_wait_for_temp_within_tolerance"):
        setattr(afc, name, MagicMock(return_value=True))
    afc._check_bypass = MagicMock(return_value=False)
    afc.number_of_toolchanges = 0
    tools = {}
    for lane_name, tool_name in (("lane1", "tool0"), ("lane2", "tool1")):
        tool = MagicMock()
        tool.name = tool.tool_key = tool_name
        tool.th_extruder_name = "extruder"
        tool.heats_on_pickup = heats_on_pickup
        lane = MagicMock()
        lane.name = lane_name
        lane.extruder_obj = tool
        lane._afc_prep_done = True
        lane.status = AFCLaneState.LOADED
        afc.lanes[lane_name] = lane
        tools[lane_name] = tool
    afc.function.get_current_lane.return_value = "lane1"
    afc.function.get_current_extruder.return_value = "tool0"
    afc.function.in_print.return_value = False
    afc._heat_next_extruder.return_value = (tools["lane2"], 200.0)
    return afc


class TestAfcChangeTool:
    def test_hotend_changer_skips_preheat_and_cooldown(self):
        afc = _change_tool_afc()
        afc.CHANGE_TOOL(afc.lanes["lane2"], new_extruder_temp=230.0)
        afc._heat_next_extruder.assert_not_called()
        afc._cooldown_last_extruder.assert_not_called()
        afc._wait_for_temp_within_tolerance.assert_not_called()
        afc.TOOL_LOAD.assert_called_once()
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_temp_available_during_load_then_cleared(self):
        afc = _change_tool_afc()
        seen = []
        afc.TOOL_LOAD.side_effect = lambda *a, **k: (seen.append(afc.next_tool_temp), True)[1]
        afc.CHANGE_TOOL(afc.lanes["lane2"], new_extruder_temp=230.0)
        assert seen == [230.0]
        assert afc.next_tool_temp is None
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_no_temp_passes_none(self):
        afc = _change_tool_afc()
        seen = []
        afc.TOOL_LOAD.side_effect = lambda *a, **k: (seen.append(afc.next_tool_temp), True)[1]
        afc.CHANGE_TOOL(afc.lanes["lane2"])
        assert seen == [None]
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_infinite_runout_resets_lane_status(self):
        afc = _change_tool_afc()
        lane = afc.lanes["lane2"]
        lane.status = AFCLaneState.INFINITE_RUNOUT
        afc.CHANGE_TOOL(lane)
        assert lane.status == AFCLaneState.LOADED
        afc._heat_next_extruder.assert_not_called()
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_infinite_runout_same_tool_keeps_status(self):
        """Infinite runout alone (no tool change, no temp) does not reset the status."""
        afc = _change_tool_afc()
        lane = afc.lanes["lane2"]
        lane.extruder_obj.tool_key = "tool0"
        lane.status = AFCLaneState.INFINITE_RUNOUT
        afc.CHANGE_TOOL(lane)
        assert lane.status == AFCLaneState.INFINITE_RUNOUT
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_new_temp_without_infinite_runout_keeps_status(self):
        """A new temperature alone does not reset the status."""
        afc = _change_tool_afc()
        lane = afc.lanes["lane2"]
        afc.CHANGE_TOOL(lane, new_extruder_temp=230.0)
        assert lane.status == AFCLaneState.LOADED
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2")]

    def test_regular_toolchanger_still_preheats(self):
        afc = _change_tool_afc(heats_on_pickup=False)
        afc.CHANGE_TOOL(afc.lanes["lane2"], new_extruder_temp=230.0)
        afc._heat_next_extruder.assert_called_once_with(wait=False, next_temp=230.0)
        assert afc.next_tool_temp is None
        assert afc.logger.messages == [("info", "Tool Change - lane1 -> lane2"),
                                       ("info", "Heating and waiting for tool1 for tool change."),
                                       ("info", "tool1 heated and ready to print")]


def _m109_afc(backend, t_lane=None, current_temp=20.0):
    afc = _make_real_afc()
    afc.function.get_current_extruder_obj.return_value = None
    afc.function.get_lane_by_map.return_value = t_lane
    afc.function.get_shared_backend.return_value = backend
    heater = MagicMock()
    heater.get_temp.return_value = (current_temp, 0.0)
    afc.toolhead.get_extruder.return_value.get_heater.return_value = heater
    if t_lane is not None:
        t_lane.extruder_obj.get_heater.return_value = heater
    heaters = afc.printer.lookup_object("heaters")
    heaters.reset_mock()
    return afc, heaters, heater


RAW_CMD = ("debug", "AFC_M104/M109 raw cmd: ")
DONE = ("debug", "Done setting temp")


def _setting(lane, temp):
    return ("debug", f"Setting temperature for {lane.extruder_obj} to {temp}")


def _indx_backend(active):
    from tests.test_AFC_indx import _make_indx
    unit, backend, _, tools = _make_indx(active=active)
    for tool in tools:
        tool.shared_extruder = True
    return unit, backend, tools


class TestAfcM109:
    def test_hotend_changer_heats_tool_on_head(self):
        _, backend, _ = _indx_backend(active=0)
        afc, heaters, heater = _m109_afc(backend)
        afc.cmd_AFC_M104(MockGCodeCommand(params={"S": 210.0}))
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 210.0, False)
        assert afc.logger.messages == [RAW_CMD, DONE]

    def test_hotend_changer_empty_head_skips(self):
        _, backend, _ = _indx_backend(active=-1)
        afc, heaters, _ = _m109_afc(backend)
        afc.cmd_AFC_M109(MockGCodeCommand(params={"S": 210.0}))
        heaters.set_temperature.assert_not_called()
        assert afc.logger.messages == [RAW_CMD]

    def test_t_for_parked_tool_skips(self):
        _, backend, tools = _indx_backend(active=0)
        afc, heaters, _ = _m109_afc(backend, t_lane=tools[1].tc_lane)
        afc.cmd_AFC_M104(MockGCodeCommand(params={"S": 210.0, "T": 1}))
        heaters.set_temperature.assert_not_called()
        assert afc.logger.messages == [RAW_CMD, _setting(tools[1].tc_lane, 210.0)]

    def test_t_for_tool_on_head_heats_and_waits(self):
        _, backend, tools = _indx_backend(active=1)
        afc, heaters, heater = _m109_afc(backend, t_lane=tools[1].tc_lane)
        afc.cmd_AFC_M109(MockGCodeCommand(params={"S": 215.0, "T": 1}))
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 215.0, True)
        assert afc.logger.messages == [RAW_CMD, _setting(tools[1].tc_lane, 215.0), DONE]

    def test_no_heat_tool_held_at_zero_without_wait(self):
        unit, backend, _ = _indx_backend(active=2)
        unit.printer._objects["gcode_macro TOOL_POSITIONS"].variables["no_heat_tools"] = [2]
        afc, heaters, heater = _m109_afc(backend)
        afc.cmd_AFC_M109(MockGCodeCommand(params={"S": 215.0}))
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 0.0, False)
        assert afc.logger.messages == [RAW_CMD, DONE]

    def test_unchanged_temp_still_waits(self):
        """filtered == temp alone keeps the M109 wait."""
        _, backend, _ = _indx_backend(active=0)
        afc, heaters, heater = _m109_afc(backend)
        afc.cmd_AFC_M109(MockGCodeCommand(params={"S": 215.0}))
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 215.0, True)
        assert afc.logger.messages == [RAW_CMD, DONE]

    def test_without_hotend_changer(self):
        afc, heaters, heater = _m109_afc(None)
        afc.cmd_AFC_M104(MockGCodeCommand(params={"S": 205.0}))
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 205.0, False)
        assert afc.logger.messages == [RAW_CMD, DONE]

    def test_t_for_regular_tool_bypasses_filter(self):
        """A T for a tool that does not share the extruder alone skips the filter."""
        backend = MagicMock()
        lane = MagicMock()
        lane.extruder_obj.shared_extruder = False
        afc, heaters, heater = _m109_afc(backend, t_lane=lane)
        afc.cmd_AFC_M104(MockGCodeCommand(params={"S": 205.0, "T": 3}))
        backend.filter_heat.assert_not_called()
        assert heaters.set_temperature.call_args_list[-1].args == (heater, 205.0, False)
        assert afc.logger.messages == [RAW_CMD, _setting(lane, 205.0), DONE]


def _load_afc():
    afc = _make_real_afc()
    afc.verify_macro_positions = MagicMock(return_value="")
    afc._check_bypass = MagicMock(return_value=False)
    afc.gcode_move = MagicMock()
    afc.gcode_move.last_position = [0.0, 0.0, 0.0, 0.0]
    afc.move_z_pos = MagicMock()
    lane = MagicMock()
    lane.name = "lane2"
    lane.extruder_obj.tool_key = "tool1"
    lane.tool_swap.return_value = False
    afc.lanes["lane2"] = lane
    afc.function.get_current_extruder.return_value = "tool0"
    return afc, lane


class TestAfcToolLoad:
    def test_failed_swap_stops_load(self):
        afc, lane = _load_afc()
        afc.load_sequence = MagicMock()
        assert afc.TOOL_LOAD(lane) is False
        lane.tool_swap.assert_called_once_with()
        afc.load_sequence.assert_not_called()
        assert afc.logger.messages == []

    def test_no_swap_when_tool_on_head(self):
        afc, lane = _load_afc()
        afc.function.get_current_extruder.return_value = "tool1"
        lane.extruder_obj.lane_loaded = "lane2"
        afc.load_sequence = MagicMock(return_value=False)
        afc.TOOL_LOAD(lane)
        lane.tool_swap.assert_not_called()
        afc.load_sequence.assert_called_once()
        assert afc.logger.messages == [("info", "Loading lane2")]


class TestAfcToolUnload:
    def test_failed_swap_stops_unload(self):
        afc, lane = _load_afc()
        afc.next_lane_load = "lane2"
        current = MagicMock()
        current.extruder_obj.tool_key = "tool0"
        afc.unload_sequence = MagicMock()
        assert afc.TOOL_UNLOAD(current) is False
        lane.tool_swap.assert_called_once_with()
        afc.unload_sequence.assert_not_called()
        assert afc.logger.messages == []

    def test_successful_swap_continues_with_new_tool(self):
        afc, lane = _load_afc()
        afc.next_lane_load = "lane2"
        lane.tool_swap.return_value = True
        new_tool = MagicMock()
        new_tool.lane_loaded = None
        new_tool.lanes = {}
        afc.function.get_current_extruder_obj.return_value = new_tool
        assert afc.TOOL_UNLOAD(MagicMock()) is True
        lane.tool_swap.assert_called_once_with()
        afc.function.get_current_extruder_obj.assert_called_once_with()
        # New tool has nothing loaded, so there is nothing left to unload
        assert afc.logger.messages == [
            ("debug", f"Current extruder: {new_tool}, current lane:None"),
            ("debug", "Next lane load:lane2, lanes:{}, current lane:None, unload_toolhead:False")]

    def test_no_swap_without_next_tool(self):
        """next_extruder None alone skips the swap."""
        afc, lane = _load_afc()
        afc.next_lane_load = "lane2"
        lane.extruder_obj.tool_key = None
        current = MagicMock()
        current.extruder_obj.lanes = {}
        assert afc.TOOL_UNLOAD(current) is True
        lane.tool_swap.assert_not_called()
        assert afc.logger.messages == [
            ("debug", f"Next lane load:lane2, lanes:{{}}, current lane:{current}, "
                      "unload_toolhead:False")]

    def test_no_swap_when_next_lane_on_current_tool(self):
        """next tool already on the head alone skips the swap."""
        afc, lane = _load_afc()
        afc.next_lane_load = "lane2"
        afc.function.get_current_extruder.return_value = "tool1"
        current = MagicMock()
        current.extruder_obj.lanes = {}
        assert afc.TOOL_UNLOAD(current) is True
        lane.tool_swap.assert_not_called()
        assert afc.logger.messages == [
            ("debug", f"Next lane load:lane2, lanes:{{}}, current lane:{current}, "
                      "unload_toolhead:False")]


class TestAfcCmdChangeBlade:
    def test_resolves_tool_by_afc_name(self):
        afc = _make_real_afc()
        tool = MagicMock()
        afc.function.get_tool.return_value = tool
        afc.cmd_AFC_CHANGE_BLADE(MockGCodeCommand(params={"EXTRUDER": "tool1"}))
        afc.function.get_tool.assert_called_once_with("tool1")
        tool.estats.cut_total_since_changed.reset_count.assert_called_once_with()
        assert afc.logger.messages == [("info", "Cutter blade stats reset for tool1")]

    def test_unknown_tool(self):
        afc = _make_real_afc()
        afc.function.get_tool.return_value = None
        afc.cmd_AFC_CHANGE_BLADE(MockGCodeCommand(params={"EXTRUDER": "nope"}))
        assert afc.logger.messages == [("error", "nope is not a valid extruder name")]


class TestAfcCmdResetStats:
    def _afc(self, tool):
        afc = _make_real_afc()
        afc.db_backup = True
        afc.function.get_tool.return_value = tool
        return afc

    def test_resolves_tool_by_afc_name(self):
        tool = MagicMock()
        tool.name = "tool1"
        afc = self._afc(tool)
        afc.cmd_AFC_RESET_STATS(MockGCodeCommand(params={"EXTRUDER": "tool1"}))
        afc.function.get_tool.assert_called_once_with("tool1")
        tool.estats.reset_stats.assert_called_once_with()
        assert afc.logger.messages == [("info", "Extruder stats reset for tool1")]

    def test_unknown_tool(self):
        afc = self._afc(None)
        afc.cmd_AFC_RESET_STATS(MockGCodeCommand(params={"EXTRUDER": "nope"}))
        assert afc.logger.messages == [("error", "nope is not a valid extruder name")]


# ═════════════════════════════════════════════════════════════════════════
# AFC_lane
# ═════════════════════════════════════════════════════════════════════════

class TestAFCLaneSyncToExtruder:
    def _lane(self, shared, on_head):
        lane = _make_real_afc_lane()
        lane.extruder_obj = MagicMock()
        lane.extruder_obj.th_extruder_name = "extruder"
        lane.extruder_obj.shared_extruder = shared
        lane.extruder_obj.on_shuttle.return_value = on_head
        lane.drive_stepper = MagicMock()
        return lane

    def test_shared_tool_off_head_not_synced(self):
        lane = self._lane(True, False)
        lane.sync_to_extruder()
        lane.drive_stepper.sync_to_extruder.assert_not_called()

    def test_shared_tool_on_head_synced(self):
        """On the head alone lets a shared tool sync."""
        lane = self._lane(True, True)
        lane.sync_to_extruder(update_current=False)
        lane.drive_stepper.sync_to_extruder.assert_called_once_with(
            False, th_extruder_name="extruder")

    def test_regular_tool_always_synced(self):
        """Not shared alone lets a tool off the head sync."""
        lane = self._lane(False, False)
        lane.sync_to_extruder()
        lane.drive_stepper.sync_to_extruder.assert_called_once_with(
            True, th_extruder_name="extruder")


class TestAFCLaneToolSwap:
    @pytest.mark.parametrize("result", [True, False])
    def test_returns_unit_result_with_next_tool_temp(self, result):
        lane = _make_real_afc_lane()
        lane.extruder_obj = MagicMock()
        lane.extruder_obj.tc_unit_obj.tool_swap.return_value = result
        lane.afc.next_tool_temp = 240.0
        assert lane.tool_swap() is result
        lane.extruder_obj.tc_unit_obj.tool_swap.assert_called_once_with(
            lane, set_start_time=False, target_temp=240.0)

    def test_without_unit(self):
        lane = _make_real_afc_lane()
        lane.extruder_obj = MagicMock()
        lane.extruder_obj.tc_unit_obj = None
        assert lane.tool_swap() is True


# ═════════════════════════════════════════════════════════════════════════
# AFC_prep
# ═════════════════════════════════════════════════════════════════════════

class TestAfcPrepActivateShuttleExtruder:
    def _prep(self, current="extruder"):
        printer = MockPrinter(afc=MockAFC())
        prep = afcPrep(MockConfig("AFC_prep", printer))
        prep.handle_connect()
        prep.afc.function.get_current_extruder.return_value = current
        return prep

    def _tool(self, on_shuttle=True, shared=False, extruder="extruder1"):
        tool = MagicMock()
        tool.on_shuttle.return_value = on_shuttle
        tool.shared_extruder = shared
        tool.th_extruder_name = extruder
        return tool

    def test_activates_tool_on_shuttle(self):
        prep = self._prep()
        prep._activate_shuttle_extruder(self._tool())
        prep.afc.gcode.run_script_from_command.assert_called_once_with(
            "ACTIVATE_EXTRUDER EXTRUDER=extruder1")

    def test_tool_off_shuttle(self):
        prep = self._prep()
        prep._activate_shuttle_extruder(self._tool(on_shuttle=False))
        prep.afc.gcode.run_script_from_command.assert_not_called()

    def test_hotend_changer_tool(self):
        """Shared extruder alone skips activation."""
        prep = self._prep()
        prep._activate_shuttle_extruder(self._tool(shared=True))
        prep.afc.gcode.run_script_from_command.assert_not_called()

    def test_already_active(self):
        """Matching active extruder alone skips activation."""
        prep = self._prep(current="extruder1")
        prep._activate_shuttle_extruder(self._tool())
        prep.afc.gcode.run_script_from_command.assert_not_called()


class TestAfcPrepPrep:
    def test_activates_each_tool(self):
        """PREP hands every tool to _activate_shuttle_extruder after setting its LED."""
        printer, afc = _make_printer()
        printer.state_message = "Printer is ready"
        prep = afcPrep(MockConfig("AFC_prep", printer))
        prep.handle_connect()
        prep._rename_macros = MagicMock()
        prep._activate_shuttle_extruder = MagicMock()
        afc.logger = prep.logger = MockLogger()
        for name in ("handle_moonraker_connect", "save_vars", "print_version"):
            setattr(afc, name, MagicMock())
        afc.function = MagicMock()
        afc.error = MagicMock()
        afc.spool = MagicMock()
        afc.bypass = MagicMock()
        afc.VarFile = "/nonexistent/AFC.var"
        tools = [MagicMock(), MagicMock()]
        for i, tool in enumerate(tools):
            tool.name = f"tool{i}"
            tool.tool_start = None
        afc.tools = {"tool0": tools[0], "tool1": tools[1]}
        prep.PREP(MockGCodeCommand())
        assert [c.args for c in prep._activate_shuttle_extruder.call_args_list] == [
            (tools[0],), (tools[1],)]
        for tool in tools:
            tool.set_status_led.assert_any_call(afc.led_tool_unloaded)
