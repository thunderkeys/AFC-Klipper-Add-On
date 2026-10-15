# Unit tests for extras/AFC_toolchanger_backend.py

from __future__ import annotations

import pytest

from extras.AFC_toolchanger_backend import KtcBackend, ToolchangerBackend
from tests.test_AFC_Toolchanger import _make_toolchanger
from tests.test_AFC_extruder import _make_extruder_obj


def _make_backend(cls=ToolchangerBackend):
    """Backend built through its real __init__ on a real AfcToolchanger unit."""
    unit = _make_toolchanger()
    backend = cls(unit)
    unit.afc.logger.messages.clear()
    return unit, backend


def _make_ktc_extruder(name="extruder", custom_swap=None, custom_unselect=None):
    ext = _make_extruder_obj(name)
    ext.custom_tool_swap = custom_swap
    ext.custom_unselect = custom_unselect
    return ext


# ── ToolchangerBackend ───────────────────────────────────────────────────────

class TestToolchangerBackendInit:
    def test_class_defaults(self):
        assert ToolchangerBackend.name == "base"
        assert ToolchangerBackend.shared_extruder is False
        assert ToolchangerBackend.heats_on_pickup is False

    def test_binds_unit_afc_and_logger(self):
        unit, backend = _make_backend()
        assert backend.unit is unit
        assert backend.afc is unit.afc
        assert backend.logger is unit.afc.logger


class TestToolchangerBackendHandleReady:
    def test_does_nothing(self):
        unit, backend = _make_backend()
        assert backend.handle_ready() is None
        assert unit.afc.logger.messages == []
        unit.afc.gcode.run_script_from_command.assert_not_called()


class TestToolchangerBackendSelect:
    def test_not_implemented(self):
        _, backend = _make_backend()
        with pytest.raises(NotImplementedError):
            backend.select(_make_ktc_extruder(), 200.0)


class TestToolchangerBackendUnselect:
    def test_not_implemented(self):
        _, backend = _make_backend()
        with pytest.raises(NotImplementedError):
            backend.unselect(None)


class TestToolchangerBackendIsOnHead:
    def test_not_implemented(self):
        _, backend = _make_backend()
        with pytest.raises(NotImplementedError):
            backend.is_on_head(_make_ktc_extruder())


class TestToolchangerBackendFilterHeat:
    @pytest.mark.parametrize("temp", [0.0, 210.0])
    def test_returns_temp_unchanged(self, temp):
        unit, backend = _make_backend()
        assert backend.filter_heat(_make_ktc_extruder(), temp, "M104") == temp
        assert backend.filter_heat(None, temp, "M109") == temp
        assert unit.afc.logger.messages == []


class TestToolchangerBackendActiveTool:
    def test_not_tracked(self):
        _, backend = _make_backend()
        assert backend.active_tool() is None


# ── KtcBackend ───────────────────────────────────────────────────────────────

class TestKtcBackendClass:
    def test_flags(self):
        assert KtcBackend.name == "ktc"
        assert KtcBackend.shared_extruder is False
        assert KtcBackend.heats_on_pickup is False


class TestKtcBackendSelect:
    def test_plain_extruder_is_tool_zero(self):
        unit, backend = _make_backend(KtcBackend)
        assert backend.select(_make_ktc_extruder("extruder")) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=0")
        assert unit.afc.logger.messages == []

    def test_numbered_extruder(self):
        unit, backend = _make_backend(KtcBackend)
        assert backend.select(_make_ktc_extruder("extruder3"), target_temp=220.0) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("SELECT_TOOL T=3")

    def test_custom_swap(self):
        unit, backend = _make_backend(KtcBackend)
        ext = _make_ktc_extruder("extruder1", custom_swap="PICK_EXTRUDER1")
        assert backend.select(ext) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PICK_EXTRUDER1")
        assert unit.afc.logger.messages == [("info", "Running custom select: PICK_EXTRUDER1")]


class TestKtcBackendUnselect:
    def test_custom_unselect(self):
        unit, backend = _make_backend(KtcBackend)
        assert backend.unselect(_make_ktc_extruder(custom_unselect="PARK_EXTRUDER")) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("PARK_EXTRUDER")
        assert unit.afc.logger.messages == [("info", "Running custom unselect: PARK_EXTRUDER")]

    def test_extruder_without_custom_unselect(self):
        """custom_unselect unset alone falls back to UNSELECT_TOOL."""
        unit, backend = _make_backend(KtcBackend)
        assert backend.unselect(_make_ktc_extruder()) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")
        assert unit.afc.logger.messages == []

    def test_no_extruder(self):
        """No extruder alone falls back to UNSELECT_TOOL."""
        unit, backend = _make_backend(KtcBackend)
        assert backend.unselect(None) is True
        unit.afc.gcode.run_script_from_command.assert_called_once_with("UNSELECT_TOOL")
        assert unit.afc.logger.messages == []


class TestKtcBackendIsOnHead:
    @pytest.mark.parametrize("detected", [True, False])
    def test_uses_extruder_detection(self, detected):
        _, backend = _make_backend(KtcBackend)
        ext = _make_ktc_extruder()
        ext.detect_on_shuttle.return_value = detected
        assert backend.is_on_head(ext) is detected
        ext.detect_on_shuttle.assert_called_once_with()
