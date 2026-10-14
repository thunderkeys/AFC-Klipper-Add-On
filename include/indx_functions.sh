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
indx_message=""

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

