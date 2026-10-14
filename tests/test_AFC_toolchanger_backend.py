# Unit tests for extras/AFC_toolchanger_backend.py

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from extras.AFC_toolchanger_backend import KtcBackend, ToolchangerBackend
from extras.AFC_Toolchanger import BACKENDS
from tests.test_AFC_Toolchanger import _make_toolchanger, _make_config_for_init
from tests.test_AFC_extruder import _make_extruder_obj


def _make_ktc_extruder(name="extruder", custom_swap=None, custom_unselect=None):
    ext = _make_extruder_obj(name)
    ext.custom_tool_swap = custom_swap
    ext.custom_unselect = custom_unselect
    return ext


class TestBackendSelection:
    def test_default_backend_is_ktc(self):
        tool = _make_toolchanger()
        assert isinstance(tool.backend, KtcBackend)

    def test_backend_option_is_case_insensitive(self):
        tool = _make_toolchanger(values={"backend": "KTC"})
        assert isinstance(tool.backend, KtcBackend)

    def test_invalid_backend_raises(self):
        from extras.AFC_Toolchanger import AfcToolchanger
        from configparser import Error
        config = _make_config_for_init(values={"backend": "nope"})
        with pytest.raises(Error, match="invalid backend 'nope'"):
            AfcToolchanger(config)

    def test_ktc_registered(self):
        assert BACKENDS["ktc"] is KtcBackend

    def test_backend_handle_ready_called_on_ready(self):
        tool = _make_toolchanger()
        tool.backend = MagicMock()
        tool.handle_ready()
        tool.backend.handle_ready.assert_called_once()


class TestBaseBackend:
    def test_defaults(self):
        backend = ToolchangerBackend(_make_toolchanger())
        assert backend.shared_extruder is False
        assert backend.heats_on_pickup is False
        assert backend.active_tool() is None

    def test_abstract_methods_raise(self):
        backend = ToolchangerBackend(_make_toolchanger())
        ext = _make_ktc_extruder()
        with pytest.raises(NotImplementedError):
            backend.select(ext)
        with pytest.raises(NotImplementedError):
            backend.unselect(ext)
        with pytest.raises(NotImplementedError):
            backend.is_on_head(ext)


class TestKtcBackend:
    def test_select_plain_extruder_is_tool_zero(self):
        tool = _make_toolchanger()
        tool.backend.select(_make_ktc_extruder("extruder"))
        tool.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=0")

    def test_select_numbered_extruder(self):
        tool = _make_toolchanger()
        tool.backend.select(_make_ktc_extruder("extruder3"), target_temp=220)
        tool.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=3")

    def test_select_custom_swap(self):
        tool = _make_toolchanger()
        tool.backend.select(_make_ktc_extruder("extruder1", custom_swap="PICK_EXTRUDER1"))
        tool.afc.gcode.run_script_from_command.assert_called_once_with("PICK_EXTRUDER1")

    def test_unselect_default(self):
        tool = _make_toolchanger()
        tool.backend.unselect(_make_ktc_extruder())
        tool.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")

    def test_unselect_none_extruder(self):
        tool = _make_toolchanger()
        tool.backend.unselect(None)
        tool.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")

    def test_unselect_custom(self):
        tool = _make_toolchanger()
        tool.backend.unselect(_make_ktc_extruder(custom_unselect="PARK_EXTRUDER"))
        tool.afc.gcode.run_script_from_command.assert_called_once_with("PARK_EXTRUDER")

    def test_is_on_head_uses_extruder_detection(self):
        tool = _make_toolchanger()
        ext = _make_ktc_extruder()
        ext.detect_on_shuttle.return_value = False
        assert tool.backend.is_on_head(ext) is False
        ext.detect_on_shuttle.assert_called_once()

    def test_flags(self):
        assert KtcBackend.shared_extruder is False
        assert KtcBackend.heats_on_pickup is False


class TestToolSwapPassesTargetTemp:
    def test_target_temp_forwarded_to_backend(self):
        from tests.test_AFC_Toolchanger import _make_toolchanger_for_tool_swap, \
            _make_lane_for_tool_swap
        tool = _make_toolchanger_for_tool_swap()
        tool.function.get_current_extruder_obj.return_value = None
        tool.backend = MagicMock()
        lane = _make_lane_for_tool_swap()
        tool.tool_swap(lane, target_temp=215.0)
        tool.backend.select.assert_called_once_with(lane.extruder_obj, 215.0)
