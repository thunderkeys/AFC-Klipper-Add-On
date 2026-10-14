# AFCProject Automated Filament Changer Software
#
# Copyright (C) 2026 AFCProject
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Toolchanger backend for the Bondtech INDX. All INDX tools share one klipper extruder (the Smart
# Head), tool changes are done by the vendored INDX macros, and the tool on the head is read from
# INDX's `active_tool` save_variable.

from __future__ import annotations

from configparser import Error as error
from typing import TYPE_CHECKING, Any, Optional

from extras.AFC_toolchanger_backend import ToolchangerBackend

if TYPE_CHECKING:
    from gcode import GCodeCommand
    from extras.AFC_Toolchanger import AfcToolchanger
    from extras.AFC_extruder import AFCExtruder
    from extras.AFC_lane import AFCLane

INDX_POSITIONS_MACRO = "gcode_macro TOOL_POSITIONS"
INDX_CHANGE_CMD = "INDX_CHANGE_TOOL"
INDX_PARK_CMD = "PARK_TOOL"


class IndxBackend(ToolchangerBackend):
    """
    Backend that drives the vendored INDX macros.
    """
    name = "indx"
    shared_extruder = True
    heats_on_pickup = True

    def __init__(self, unit: AfcToolchanger) -> None:
        """
        :param unit: AFC_Toolchanger unit that owns this backend
        """
        super().__init__(unit)
        self.printer = unit.printer
        self.save_vars_obj: Any = None
        self.positions_macro: Any = None
        # Set while AFC is driving a change so the macro hook does not resync mid change
        self.afc_change_active: bool = False

        self.afc.gcode.register_command("AFC_INDX_TOOL_CHANGED", self.cmd_AFC_INDX_TOOL_CHANGED,
                                        desc=self.cmd_AFC_INDX_TOOL_CHANGED_help)

    def tools(self) -> list[AFCExtruder]:
        """
        Tools that belong to this INDX unit.

        :return list: AFC_extruder objects attached to this unit
        """
        return [tool for tool in self.afc.tools.values() if tool.tc_unit_obj is self.unit]

    def handle_ready(self) -> None:
        """
        Resolves save_variables and the INDX macros, then validates tool config.
        """
        unit_name = f"AFC_Toolchanger {self.unit}"
        self.save_vars_obj = self.printer.lookup_object("save_variables", None)
        if self.save_vars_obj is None:
            error_str = (f"[{unit_name}] backend: indx requires a [save_variables] section, "
                         "INDX stores the tool on the head there.")
            raise error(error_str)

        self.positions_macro = self.printer.lookup_object(INDX_POSITIONS_MACRO, None)
        if self.positions_macro is None:
            error_str = (f"[{unit_name}] backend: indx requires the INDX macros. "
                         f"[{INDX_POSITIONS_MACRO}] was not found, make sure the AFC INDX macro "
                         "files are included in your config.")
            raise error(error_str)

        tool_count = int(self.positions_macro.variables.get("tool_count", 0))
        seen: dict[int, str] = {}
        extruder_names = set()
        for tool in self.tools():
            extruder_names.add(tool.th_extruder_name)
            if tool.tool_index is None:
                error_str = f"[{tool.fullname}] tool_index is required when using INDX."
                raise error(error_str)
            if tool.tool_index >= tool_count:
                error_str = (f"[{tool.fullname}] tool_index {tool.tool_index} is not below "
                             f"INDX tool_count ({tool_count}) set in indx.cfg.")
                raise error(error_str)
            if tool.tool_index in seen:
                error_str = (f"[{tool.fullname}] and [AFC_extruder {seen[tool.tool_index]}] "
                             f"both use tool_index {tool.tool_index}.")
                raise error(error_str)
            seen[tool.tool_index] = tool.name

        if len(extruder_names) > 1:
            error_str = (f"[{unit_name}] INDX tools must all use the same extruder_name, "
                         f"found: {', '.join(sorted(extruder_names))}")
            raise error(error_str)

        self.update_tool_macros()

    def active_index(self) -> int:
        """
        Reads INDX's `active_tool` save_variable.

        :return int: Tool number on the head, -1 when the head is empty or state is unknown
        """
        if self.save_vars_obj is None:
            return -1
        variables = self.save_vars_obj.get_status(None).get("variables", {})
        try:
            return int(variables.get("active_tool", -1))
        except (TypeError, ValueError):
            return -1

    def active_tool(self) -> Optional[AFCExtruder]:
        """
        Tool on the head according to INDX.

        :return AFCExtruder|None: Tool on the head, None if empty or not configured in AFC
        """
        index = self.active_index()
        if index < 0:
            return None
        for tool in self.tools():
            if tool.tool_index == index:
                return tool
        return None

    def is_on_head(self, extruder: AFCExtruder) -> bool:
        """
        :param extruder: AFC_extruder to check
        :return bool: True if INDX reports this tool on the head
        """
        return (extruder.tool_index is not None
                and extruder.tool_index == self.active_index())

    def select(self, extruder: AFCExtruder, target_temp: Optional[float]=None) -> bool:
        """
        Picks up a tool with INDX_CHANGE_TOOL, heating it to target_temp after pickup.

        :param extruder: AFC_extruder to select
        :param target_temp: Temperature for the incoming tool, None to let INDX fall back to the
            current extruder target
        :return bool: True if INDX reports the tool on the head afterwards
        """
        cmd = f"{INDX_CHANGE_CMD} TOOL={extruder.tool_index}"
        if target_temp is not None:
            cmd += f" TEMP={target_temp:.1f}"
        return self._run_change(cmd, extruder)

    def unselect(self, extruder: Optional[AFCExtruder]) -> bool:
        """
        Parks the tool on the head with PARK_TOOL.

        :param extruder: AFC_extruder currently on the head, unused since INDX tracks it
        :return bool: True if INDX reports an empty head afterwards
        """
        return self._run_change(INDX_PARK_CMD, None)

    def _run_change(self, cmd: str, expected: Optional[AFCExtruder]) -> bool:
        """
        Runs an INDX tool change macro and verifies the resulting head state. INDX raises or
        pauses on its own failures, AFC only reports and stops its own sequence.

        :param cmd: Macro command to run
        :param expected: Tool expected on the head afterwards, None for an empty head
        :return bool: True if the head state matches what was expected
        """
        self.afc_change_active = True
        try:
            self.afc.gcode.run_script_from_command(cmd)
        except Exception as e:
            self.afc.error.AFC_error(f"INDX tool change failed: {e}",
                                     pause=self.afc.function.in_print())
            return False
        finally:
            self.afc_change_active = False
            self.update_tool_macros()

        active = self.active_tool()
        if active is not expected:
            expected_str = f"T{expected.tool_index}" if expected is not None else "empty head"
            active_str = f"T{self.active_index()}" if self.active_index() >= 0 else "empty head"
            self.afc.error.AFC_error(f"INDX reports {active_str} after '{cmd}', expected "
                                     f"{expected_str}.", pause=self.afc.function.in_print())
            return False
        return True

    def _tool_lane(self, tool: AFCExtruder) -> Optional[AFCLane]:
        """
        Lane whose T(n) map represents this tool: the loaded lane, or the standalone lane.

        :param tool: AFC_extruder to look up
        :return AFCLane|None: Lane for the tool, None if there is none
        """
        lane_name = tool.lane_loaded
        if lane_name is None and tool.tc_lane is not None:
            lane_name = tool.tc_lane.name
        if lane_name is None:
            return None
        return self.afc.lanes.get(lane_name)

    def update_tool_macros(self) -> None:
        """
        Sets `active` on the T(n) wrapper macros so Mainsail/Fluidd highlight the T(n) mapped to
        the tool on the head. Macros without an `active` variable are left alone.
        """
        active = self.active_tool()
        active_lane = self._tool_lane(active) if active is not None else None
        tools = self.tools()
        for lane in self.afc.lanes.values():
            if lane.extruder_obj not in tools:
                continue
            for cmd in lane.map or []:
                macro = self.printer.lookup_object(f"gcode_macro {cmd}", None)
                if (macro is None
                    or "active" not in macro.variables):
                    continue
                variables = dict(macro.variables)
                variables["active"] = lane is active_lane
                macro.variables = variables

    cmd_AFC_INDX_TOOL_CHANGED_help = "Internal: called by the INDX macros when active_tool changes"
    def cmd_AFC_INDX_TOOL_CHANGED(self, gcmd: GCodeCommand) -> None:
        """
        Called by the vendored `_INDX_SET_ACTIVE_TOOL` macro whenever INDX records a new tool on
        the head, so AFC stays in sync with changes made outside AFC (homing T0 fetch, manual
        seat/remove, calibration). Changes started by AFC are synced by AFC itself.

        Usage
        -----
        `AFC_INDX_TOOL_CHANGED TOOL=<n>`

        Example
        -----
        ```
        AFC_INDX_TOOL_CHANGED TOOL=1
        ```
        """
        tool = gcmd.get_int("TOOL", -1)
        if self.afc_change_active:
            return
        self.update_tool_macros()
        # INDX restores its state shortly after startup, PREP syncs lanes once it runs
        if not self.afc.prep_done:
            return
        self.logger.debug(f"INDX reported T{tool} on head, syncing AFC")
        self.afc.function.handle_activate_extruder()
