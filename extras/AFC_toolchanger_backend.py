# Armored Turtle Automated Filament Control
#
# Copyright (C) 2025 Armored Turtle
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Toolchanger backends used by AFC_Toolchanger. A backend owns how a tool is physically picked
# up and parked, and how AFC detects which tool is on the head.

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from extras.AFC_Toolchanger import AfcToolchanger
    from extras.AFC_extruder import AFCExtruder
    from extras.AFC import afc
    from extras.AFC_logger import AFC_logger


class ToolchangerBackend:
    """
    Base class for toolchanger backends.

    `shared_extruder` is True for hotend changers (e.g. INDX): toolchangers that swap only the
    hotend while the extruder and heater stay on the head, so every tool drives the same klipper
    extruder and tool identity cannot be derived from the active klipper extruder.
    `heats_on_pickup` is True when the incoming tool cannot be preheated and the outgoing tool
    cannot stay hot.
    """
    name: str = "base"
    shared_extruder: bool = False
    heats_on_pickup: bool = False

    def __init__(self, unit: AfcToolchanger) -> None:
        """
        :param unit: AFC_Toolchanger unit that owns this backend
        """
        self.unit = unit
        self.afc: afc = unit.afc
        self.logger: AFC_logger = unit.afc.logger

    def handle_ready(self) -> None:
        """
        Called once klipper is ready, used to validate config and resolve objects.
        """
        return

    def select(self, extruder: AFCExtruder, target_temp: Optional[float]=None) -> bool:
        """
        Pick up the tool for the given extruder.

        :param extruder: AFC_extruder to select
        :param target_temp: Temperature the incoming tool should heat to, None to let the
            backend decide
        :return bool: True if the tool was picked up
        """
        raise NotImplementedError

    def unselect(self, extruder: Optional[AFCExtruder]) -> bool:
        """
        Park the tool currently on the head.

        :param extruder: AFC_extruder currently on the head, None if unknown
        :return bool: True if the tool was parked
        """
        raise NotImplementedError

    def is_on_head(self, extruder: AFCExtruder) -> bool:
        """
        Report whether the given tool is currently on the head/shuttle.

        :param extruder: AFC_extruder to check
        :return bool: True if the tool is on the head
        """
        raise NotImplementedError

    def filter_heat(self, tool: Optional[AFCExtruder], temp: float, cmd: str) -> Optional[float]:
        """
        Adjusts an M104/M109 request for backends with a shared heater. Base backend leaves it
        unchanged.

        :param tool: Tool targeted by the T parameter, None when no T was given
        :param temp: Requested temperature
        :param cmd: Command name for console messages
        :return float|None: Temperature to apply, None to skip the request entirely
        """
        return temp

    def active_tool(self) -> Optional[AFCExtruder]:
        """
        Return the tool on the head when the backend tracks it independently of the active
        klipper extruder. Only meaningful for shared extruder backends.

        :return AFCExtruder|None: Tool on the head, None if empty or not tracked
        """
        return None


class KtcBackend(ToolchangerBackend):
    """
    Backend for klipper-toolchanger (KTC) and setups using custom swap/unselect macros (U1).
    This is the original AFC_Toolchanger behavior.
    """
    name = "ktc"

    def select(self, extruder: AFCExtruder, target_temp: Optional[float]=None) -> bool:
        """
        Run the extruder's custom swap macro, or KTC SELECT_TOOL using the index parsed from
        the klipper extruder name.

        :param extruder: AFC_extruder to select
        :param target_temp: Unused, KTC tools have their own heaters
        :return bool: Always True, KTC failures raise
        """
        name = extruder.th_extruder_name
        tool_index = 0 if name == "extruder" else int(name.replace("extruder", ""))

        if extruder.custom_tool_swap:
            self.logger.info(f"Running custom select: {extruder.custom_tool_swap}")
            self.afc.gcode.run_script_from_command(f"{extruder.custom_tool_swap}")
        else:
            self.afc.gcode.run_script_from_command(f"SELECT_TOOL T={tool_index}")
        return True

    def unselect(self, extruder: Optional[AFCExtruder]) -> bool:
        """
        Run the extruder's custom unselect macro, or KTC UNSELECT_TOOL.

        :param extruder: AFC_extruder currently on the head, None if unknown
        :return bool: Always True, KTC failures raise
        """
        if (extruder
            and extruder.custom_unselect):
            self.logger.info(f"Running custom unselect: {extruder.custom_unselect}")
            self.afc.gcode.run_script_from_command(f"{extruder.custom_unselect}")
        else:
            self.afc.gcode.run_script_from_command("UNSELECT_TOOL")
        return True

    def is_on_head(self, extruder: AFCExtruder) -> bool:
        """
        Uses KTC tool detection or the U1 park detector.

        :param extruder: AFC_extruder to check
        :return bool: True if the tool is on the shuttle
        """
        return extruder.detect_on_shuttle()

