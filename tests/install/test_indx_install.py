"""Tests for the Bondtech INDX installer helpers (include/indx_functions.sh)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PRELUDE = f"""
set -euo pipefail
REPO_ROOT="{REPO_ROOT}"
source "$REPO_ROOT/include/units/registry.sh"
source "$REPO_ROOT/include/constants.sh"
export afc_path="$REPO_ROOT"
source "$REPO_ROOT/include/colors.sh"
source "$REPO_ROOT/include/utils.sh"
source "$REPO_ROOT/include/buffer_configurations.sh"
source "$REPO_ROOT/include/install_functions.sh"
source "$REPO_ROOT/include/indx_functions.sh"
printer_config_dir="$CFG"
afc_config_dir="$CFG/AFC"
mkdir -p "$afc_config_dir"
"""


def _run(tmp_path: Path, script: str, stdin: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["CFG"] = str(cfg)
    proc = subprocess.run(["bash", "-c", PRELUDE + script], env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc, cfg


def test_fresh_install_copies_everything(tmp_path):
    _, cfg = _run(tmp_path, "install_indx_files")
    afc = cfg / "AFC"
    for name in ("indx.cfg", "indx-tc-macros.cfg", "indx-cal.cfg", "homing.cfg"):
        assert (afc / "indx" / name).read_text() == \
            (REPO_ROOT / "templates" / "indx_macros" / name).read_text()
    hw = (afc / "AFC_Hardware.cfg").read_text()
    assert "[AFC_Toolchanger INDX]" in hw
    assert "\n[save_variables]\n" in hw
    assert f"filename: {cfg}/indx_vars.cfg" in hw
    assert "\n[respond]\n" in hw
    assert "\n[include indx/homing.cfg]" in hw


def test_existing_sections_reconciled(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "printer.cfg").write_text(
        "[save_variables]\nfilename: ~/vars.cfg\n\n[respond]\n\n[homing_override]\ngcode:\n  G28\n")
    proc, _ = _run(tmp_path, 'install_indx_files\necho "$indx_message"')
    hw = (cfg / "AFC" / "AFC_Hardware.cfg").read_text()
    assert "\n[save_variables]\n" not in hw
    assert "\n[respond]\n" not in hw
    assert "#[include indx/homing.cfg]" in hw
    assert "[AFC_Toolchanger INDX]" in hw
    assert "existing [save_variables]" in proc.stdout
    assert "existing [homing_override]" in proc.stdout


def test_commented_sections_ignored(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "printer.cfg").write_text("#[save_variables]\n#[homing_override]\n")
    _run(tmp_path, "install_indx_files")
    hw = (cfg / "AFC" / "AFC_Hardware.cfg").read_text()
    assert "\n[save_variables]\n" in hw
    assert "\n[include indx/homing.cfg]" in hw


def test_existing_user_indx_cfg_kept(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "AFC" / "indx").mkdir(parents=True)
    (cfg / "AFC" / "indx" / "indx.cfg").write_text("my settings\n")
    proc, _ = _run(tmp_path, 'install_indx_files\necho "$indx_message"')
    assert (cfg / "AFC" / "indx" / "indx.cfg").read_text() == "my settings\n"
    assert "Kept your existing" in proc.stdout


def test_update_reports_missing_user_settings(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "AFC" / "indx").mkdir(parents=True)
    template = (REPO_ROOT / "templates" / "indx_macros" / "indx.cfg").read_text()
    (cfg / "AFC" / "indx" / "indx.cfg").write_text(
        template.replace("variable_seat_check_enabled", "#gone"))
    for name in ("indx-tc-macros.cfg", "indx-cal.cfg", "homing.cfg"):
        (cfg / "AFC" / "indx" / name).write_text("old\n")
    # safe_copy prompts before overwriting, answer overwrite for each file
    proc, _ = _run(tmp_path, 'update_message=""\nupdate_indx_macros\necho "$update_message"',
                   stdin="o\no\no\n")
    assert "variable_seat_check_enabled" in proc.stdout
    assert (cfg / "AFC" / "indx" / "indx-tc-macros.cfg").read_text() != "old\n"


def test_update_noop_without_indx(tmp_path):
    proc, cfg = _run(tmp_path, 'update_message=""\nupdate_indx_macros\necho "[$update_message]"')
    assert "[]" in proc.stdout
    assert not (cfg / "AFC" / "indx").exists()


def test_indx_not_offered_as_additional_unit(tmp_path):
    proc, _ = _run(tmp_path, 'printf "%s\\n" "${additional_installation_options[@]}"')
    types = proc.stdout.splitlines()
    assert "Bondtech INDX" not in types
    assert "BoxTurtle (4-Lane)" in types


def test_copy_unit_files_dispatches_to_indx(tmp_path):
    _, cfg = _run(tmp_path, 'installation_type="Bondtech INDX"\ncopy_unit_files')
    assert (cfg / "AFC" / "indx" / "indx-tc-macros.cfg").exists()
    assert "[AFC_Toolchanger INDX]" in (cfg / "AFC" / "AFC_Hardware.cfg").read_text()


def test_install_with_six_tools(tmp_path):
    proc, cfg = _run(tmp_path, 'indx_tool_count=6\ninstallation_type="Bondtech INDX"\n'
                               'copy_unit_files\necho "$indx_message"')
    hw = (cfg / "AFC" / "AFC_Hardware.cfg").read_text()
    for i in range(6):
        assert f"[AFC_extruder tool{i}]" in hw
    assert "variable_tool_count: 6" in (cfg / "AFC" / "indx" / "indx.cfg").read_text()
    assert "INDX tool count set to 6" in proc.stdout


def test_install_default_three_tools(tmp_path):
    _, cfg = _run(tmp_path, 'installation_type="Bondtech INDX"\ncopy_unit_files')
    hw = (cfg / "AFC" / "AFC_Hardware.cfg").read_text()
    assert "[AFC_extruder tool2]" in hw
    assert "[AFC_extruder tool3]" not in hw


def test_prompt_accepts_and_rejects(tmp_path):
    proc, _ = _run(tmp_path, 'indx_tool_count_prompt\necho "ok=$indx_prompt_ok n=$indx_tool_count"',
                   stdin="6\n")
    assert "ok=True n=6" in proc.stdout
    proc, _ = _run(tmp_path, 'indx_tool_count_prompt\necho "ok=$indx_prompt_ok n=$indx_tool_count"',
                   stdin="abc\n")
    assert "ok=False n=3" in proc.stdout
    proc, _ = _run(tmp_path, 'indx_tool_count_prompt\necho "ok=$indx_prompt_ok n=$indx_tool_count"',
                   stdin="\n")
    assert "ok=True n=3" in proc.stdout


def test_utilities_extend_existing_install(tmp_path):
    _run(tmp_path, 'installation_type="Bondtech INDX"\ncopy_unit_files')
    proc, cfg = _run(tmp_path, 'indx_change_tool_count\necho "$unit_message"', stdin="6\n")
    hw = (cfg / "AFC" / "AFC_Hardware.cfg").read_text()
    assert "[AFC_extruder tool5]" in hw
    assert "Restart Klipper" in proc.stdout


def test_utilities_defaults_to_installed_count(tmp_path):
    _run(tmp_path, 'indx_tool_count=5\ninstallation_type="Bondtech INDX"\ncopy_unit_files')
    # Empty answer keeps the count read from the installed indx.cfg
    proc, _ = _run(tmp_path, 'indx_change_tool_count\necho "n=$indx_tool_count"', stdin="\n")
    assert "n=5" in proc.stdout


def test_utilities_without_indx(tmp_path):
    proc, _ = _run(tmp_path, 'indx_change_tool_count\necho "$unit_message"')
    assert "No INDX install found" in proc.stdout
