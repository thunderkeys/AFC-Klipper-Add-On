# Unit tests for shared extruder tool identity (multiple AFC_extruder sections on one klipper
# extruder, used by hotend changers such as INDX).

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from tests.test_AFC_extruder import _make_afc_extruder, KlipperError
from tests.test_AFC_functions import _make_func


def _shared_unit():
    unit = MagicMock()
    unit.backend.shared_extruder = True
    return unit


def _make_shared_tool(name, unit, afc=None):
    ext = _make_afc_extruder(name)
    ext.th_extruder_name = "extruder"
    ext.tc_unit_name = "INDX"
    ext.tc_unit_obj = unit
    ext.tc_lane = MagicMock()
    if afc is not None:
        ext.afc = afc
    return ext


class TestToolKey:
    def test_regular_tool_keyed_by_extruder_name(self):
        ext = _make_afc_extruder("e1")
        ext.th_extruder_name = "extruder1"
        assert ext.shared_extruder is False
        assert ext.tool_key == "extruder1"

    def test_ktc_unit_keyed_by_extruder_name(self):
        ext = _make_afc_extruder("e1")
        ext.th_extruder_name = "extruder1"
        ext.tc_unit_obj = MagicMock()
        ext.tc_unit_obj.backend.shared_extruder = False
        assert ext.shared_extruder is False
        assert ext.tool_key == "extruder1"

    def test_shared_tool_keyed_by_afc_name(self):
        ext = _make_shared_tool("T2", _shared_unit())
        assert ext.shared_extruder is True
        assert ext.tool_key == "T2"


class TestRegisterTool:
    def test_shared_tools_register_under_afc_names(self):
        unit = _shared_unit()
        t0 = _make_shared_tool("T0", unit)
        t1 = _make_shared_tool("T1", unit, afc=t0.afc)
        t0._register_tool()
        t1._register_tool()
        assert t0.afc.tools == {"T0": t0, "T1": t1}

    def test_shared_tools_in_different_units_rejected(self):
        t0 = _make_shared_tool("T0", _shared_unit())
        t1 = _make_shared_tool("T1", _shared_unit(), afc=t0.afc)
        t0._register_tool()
        with pytest.raises(KlipperError, match="Only tools in the same hotend changer"):
            t1._register_tool()

    def test_regular_tool_on_shared_extruder_rejected(self):
        t0 = _make_shared_tool("T0", _shared_unit())
        regular = _make_afc_extruder("extruder")
        regular.afc = t0.afc
        t0._register_tool()
        with pytest.raises(KlipperError, match="Only tools in the same hotend changer"):
            regular._register_tool()

    def test_shared_tool_after_regular_tool_rejected(self):
        regular = _make_afc_extruder("extruder")
        t0 = _make_shared_tool("T0", _shared_unit(), afc=regular.afc)
        regular._register_tool()
        with pytest.raises(KlipperError, match="Only tools in the same hotend changer"):
            t0._register_tool()

    def test_handle_connect_looks_up_unit_before_registering(self):
        unit = _shared_unit()
        ext = _make_shared_tool("T0", unit)
        ext.tc_unit_obj = None
        ext.printer.lookup_object = MagicMock(
            side_effect=lambda name, default=None: unit if name == "AFC_Toolchanger INDX"
            else MagicMock())
        ext.handle_connect()
        assert ext.tc_unit_obj is unit
        assert ext.afc.tools["T0"] is ext

    def test_handle_connect_missing_unit_raises(self):
        ext = _make_shared_tool("T0", _shared_unit())
        ext.tc_unit_obj = None
        ext.printer.lookup_object = MagicMock(
            side_effect=lambda name, default=None: None if name.startswith("AFC_Toolchanger")
            else MagicMock())
        with pytest.raises(KlipperError, match="AFC_Toolchanger INDX"):
            ext.handle_connect()


class TestGetCurrentExtruderShared:
    def _func_with_tools(self, active_name):
        func = _make_func()
        unit = _shared_unit()
        tools = {}
        for name in ("T0", "T1"):
            tool = MagicMock()
            tool.name = tool.tool_key = name
            tool.th_extruder_name = "extruder"
            tool.shared_extruder = True
            tool.tc_unit_obj = unit
            tools[name] = tool
        unit.backend.active_tool.return_value = tools.get(active_name)
        func.afc.tools = tools
        func.afc.toolhead = MagicMock()
        func.afc.toolhead.get_extruder.return_value.name = "extruder"
        return func, tools

    def test_returns_active_tool_key(self):
        func, tools = self._func_with_tools("T1")
        assert func.get_current_extruder() == "T1"
        assert func.get_current_extruder_obj() is tools["T1"]

    def test_returns_none_when_head_empty(self):
        func, _ = self._func_with_tools(None)
        assert func.get_current_extruder() is None
        assert func.get_current_extruder_obj() is None

    def test_returns_none_for_unknown_extruder(self):
        func, _ = self._func_with_tools("T0")
        func.afc.toolhead.get_extruder.return_value.name = "extruder5"
        assert func.get_current_extruder() is None


class TestGetTool:
    def test_by_key(self):
        func = _make_func()
        tool = MagicMock()
        func.afc.tools = {"extruder1": tool}
        assert func.get_tool("extruder1") is tool

    def test_by_afc_name(self):
        func = _make_func()
        tool = MagicMock()
        tool.name = "e1"
        func.afc.tools = {"extruder1": tool}
        assert func.get_tool("e1") is tool

    def test_not_found(self):
        func = _make_func()
        tool = MagicMock()
        tool.name = "e1"
        func.afc.tools = {"extruder1": tool}
        assert func.get_tool("nope") is None
