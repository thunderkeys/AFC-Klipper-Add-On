#!/usr/bin/env bash
# AFCProject Automated Filament Changer Software
#
# Copyright (C) 2026 AFCProject
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Bondtech INDX install/update helpers. The INDX macros are vendored in
# templates/indx_macros and installed to ${afc_config_dir}/indx/.

# Macro files AFC owns and refreshes on update. indx.cfg is the user's own
# settings file and is only copied when it does not exist yet.
INDX_MACRO_FILES=("indx-tc-macros.cfg" "indx-cal.cfg" "homing.cfg")
INDX_USER_FILE="indx.cfg"
INDX_MAX_TOOLS=32
indx_message=""
# Number of INDX tools to configure on install, set from the install menu
indx_tool_count="${indx_tool_count:-3}"

indx_find_section() {
  # Prints the config files outside ${afc_config_dir} that define section $1
  # (an exact section name, e.g. "save_variables"). Prints nothing if none.
  local section="$1"
  grep -rlsE --include='*.cfg' "^\[${section}\]" "${printer_config_dir}" 2>/dev/null \
    | grep -v "^${afc_config_dir}/" || true
}

indx_reconcile_hardware_cfg() {
  # Adjusts the installed AFC_Hardware.cfg for sections the user already has
  # elsewhere in their config, since Klipper merges duplicate sections.
  # Arguments:
  #   $1: hardware_file - path to the installed AFC_Hardware.cfg
  local hardware_file="$1"
  local existing

  sed -i "s|^filename: ~/printer_data/config/indx_vars.cfg|filename: ${printer_config_dir}/indx_vars.cfg|" "$hardware_file"

  existing="$(indx_find_section "save_variables")"
  if [ -n "$existing" ]; then
    remove_buffer_section "$hardware_file" "[save_variables]"
    indx_message+="""
- Found an existing [save_variables] in ${existing}. INDX will store its state there.
"""
  fi

  existing="$(indx_find_section "respond")"
  if [ -n "$existing" ]; then
    remove_buffer_section "$hardware_file" "[respond]"
  fi

  existing="$(indx_find_section "homing_override")"
  if [ -n "$existing" ]; then
    sed -i "s|^\[include indx/homing.cfg\]|#[include indx/homing.cfg]|" "$hardware_file"
    indx_message+="""
${RED}- Found an existing [homing_override] in ${existing}. The INDX homing override was NOT
  included. Merge the sequence from ${afc_config_dir}/indx/homing.cfg into yours, then
  remove your copy and uncomment [include indx/homing.cfg] in AFC_Hardware.cfg.${NC}
"""
  fi
}

install_indx_files() {
  # Copies the INDX hardware template and the vendored INDX macros for a
  # first-time install.
  local indx_dir="${afc_config_dir}/indx"
  local hardware_file="${afc_config_dir}/AFC_Hardware.cfg"
  local file

  mkdir -p "$indx_dir"
  for file in "${INDX_MACRO_FILES[@]}"; do
    safe_copy "${afc_path}/templates/indx_macros/${file}" "${indx_dir}/${file}"
  done
  if [ ! -f "${indx_dir}/${INDX_USER_FILE}" ]; then
    cp "${afc_path}/templates/indx_macros/${INDX_USER_FILE}" "${indx_dir}/${INDX_USER_FILE}"
  else
    indx_message+="""
- Kept your existing ${indx_dir}/${INDX_USER_FILE}.
"""
  fi

  safe_copy "${afc_path}/templates/AFC_Hardware_INDX.cfg" "$hardware_file"
  if [ "$safe_copy_result" = "copied" ]; then
    indx_reconcile_hardware_cfg "$hardware_file"
  fi
}

indx_missing_user_variables() {
  # Prints indx.cfg variable_ names present in the AFC template but missing
  # from the user's installed indx.cfg, one per line.
  local template="${afc_path}/templates/indx_macros/${INDX_USER_FILE}"
  local installed="${afc_config_dir}/indx/${INDX_USER_FILE}"
  [ -f "$installed" ] || return 0
  comm -23 \
    <(grep -oE '^variable_[A-Za-z0-9_]+' "$template" | sort -u) \
    <(grep -oE '^variable_[A-Za-z0-9_]+' "$installed" | sort -u)
}

update_indx_macros() {
  # Refreshes the AFC owned INDX macro files on update. Never touches the
  # user's indx.cfg, but reports settings it is missing.
  local indx_dir="${afc_config_dir}/indx"
  local file missing
  [ -d "$indx_dir" ] || return 0

  for file in "${INDX_MACRO_FILES[@]}"; do
    safe_copy "${afc_path}/templates/indx_macros/${file}" "${indx_dir}/${file}"
  done

  missing="$(indx_missing_user_variables)"
  if [ -n "$missing" ]; then
    update_message+="""
${RED}INDX: your ${indx_dir}/${INDX_USER_FILE} is missing these settings, copy them (with
their comments) from ${afc_path}/templates/indx_macros/${INDX_USER_FILE}:
${missing}${NC}
"""
  else
    update_message+="""
INDX macros updated.
"""
  fi
}

indx_valid_tool_count() {
  # True (0) if $1 is a whole number between 1 and INDX_MAX_TOOLS.
  [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le "$INDX_MAX_TOOLS" ]
}

indx_set_tool_count() {
  # Sets the number of INDX tools in the installed config: [AFC_extruder toolN]
  # sections in AFC_Hardware.cfg, plus tool_count, dock position placeholders
  # and T(n) wrappers in indx/indx.cfg (utilities/indx_tools.py).
  # Arguments:
  #   $1: count - number of tools
  # Appends the result to $indx_message. Returns 1 if the update failed.
  local count="$1"
  local py="${PYTHON:-python3}"
  local output
  if ! output="$("$py" "${afc_path}/utilities/indx_tools.py" --config-dir "${afc_config_dir}" \
      set-count "$count" 2>&1)"; then
    indx_message+="""
${RED}- Failed to set the INDX tool count to ${count}:
${output}${NC}
"""
    return 1
  fi
  indx_message+="""
- INDX tool count set to ${count}:
${output}
"""
}

indx_tool_count_prompt() {
  # Asks for the number of INDX tools and stores it in indx_tool_count. Sets
  # indx_prompt_ok to True or False and $message. Always returns 0, menus call
  # it as a bare statement under `set -e`.
  local input
  read -p "Enter the number of INDX tools (1-${INDX_MAX_TOOLS}) [${indx_tool_count}]: " input \
    || true
  input="${input:-$indx_tool_count}"
  if indx_valid_tool_count "$input"; then
    indx_tool_count="$input"
    indx_prompt_ok="True"
    message="INDX tool count: ${indx_tool_count}"
  else
    indx_prompt_ok="False"
    message="${RED}Invalid tool count '${input}', enter a number from 1 to ${INDX_MAX_TOOLS}${NC}"
  fi
}

indx_change_tool_count() {
  # Utilities menu: change the tool count of an existing INDX install. The
  # utilities menu shows $unit_message.
  local current
  if [ ! -f "${afc_config_dir}/indx/${INDX_USER_FILE}" ]; then
    unit_message="${RED}No INDX install found in ${afc_config_dir}/indx${NC}"
    return 0
  fi
  current="$(grep -oE '^variable_tool_count:[[:space:]]*[0-9]+' \
    "${afc_config_dir}/indx/${INDX_USER_FILE}" | grep -oE '[0-9]+$' || true)"
  indx_tool_count="${current:-$indx_tool_count}"
  indx_tool_count_prompt
  if [ "$indx_prompt_ok" != "True" ]; then
    unit_message="$message"
    return 0
  fi
  indx_message=""
  if indx_set_tool_count "$indx_tool_count"; then
    unit_message="${indx_message}
Restart Klipper to apply."
  else
    unit_message="$indx_message"
  fi
}
