# Tests for utilities/indx_resync.py using throwaway git repos as the INDX upstream.

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "indx_resync", Path(__file__).resolve().parent.parent / "utilities" / "indx_resync.py")
resync = importlib.util.module_from_spec(_SPEC)
sys.modules["indx_resync"] = resync
_SPEC.loader.exec_module(resync)


BASE_TC = """\
[gcode_macro INDEPENDENT]
gcode:
    RESPOND MSG="independent"
    G4 P10

[gcode_macro _INDX_SET_ACTIVE_TOOL]
gcode:
    SAVE_VARIABLE VARIABLE=active_tool VALUE={params.TOOL}
    {LOOP}

[gcode_macro _TC_LATCH_MOVE]
gcode:
    {% set e = params.E|float %}
    INDX_EXTRUDER_MOVE DISTANCE={e}

[gcode_macro TC_CYCLE_ALL]
gcode:
    RESPOND MSG="cycling"
    CHANGE_TOOL TOOL=0
    PARK_TOOL
"""

PATCHED_TC = """\
[gcode_macro INDEPENDENT]
gcode:
    RESPOND MSG="independent"
    G4 P10

[gcode_macro _INDX_SET_ACTIVE_TOOL]
gcode:
    SAVE_VARIABLE VARIABLE=active_tool VALUE={params.TOOL}
    # AFC-PATCH: notify AFC
    AFC_INDX_TOOL_CHANGED TOOL={params.TOOL}

[gcode_macro _TC_LATCH_MOVE]
gcode:
    {% set e = params.E|float %}
    # AFC-PATCH: unsync lanes
    AFC_INDX_PRE_LATCH
    INDX_EXTRUDER_MOVE DISTANCE={e}

[gcode_macro TC_CYCLE_ALL]
gcode:
    RESPOND MSG="cycling"
    # AFC-PATCH: INDX_CHANGE_TOOL
    INDX_CHANGE_TOOL TOOL=0
    PARK_TOOL
"""

BASE_USER = """\
[gcode_macro TOOL_POSITIONS]
variable_tool_count: 2
variable_z_hop: 1.0
gcode:
"""


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path):
    """Upstream INDX repo with one commit, an AFC root and a vendored macro dir."""
    indx = tmp_path / "INDX"
    (indx / "macros").mkdir(parents=True)
    _git(indx, "init", "-q")
    _git(indx, "config", "user.email", "t@t")
    _git(indx, "config", "user.name", "t")
    _git(indx, "remote", "add", "origin", "https://example.invalid/INDX")
    (indx / "macros" / "indx-tc-macros.cfg").write_text(BASE_TC)
    (indx / "macros" / "indx.cfg").write_text(BASE_USER)
    _git(indx, "add", ".")
    _git(indx, "commit", "-qm", "base")
    base_commit = subprocess.run(["git", "-C", str(indx), "rev-parse", "HEAD"], check=True,
                                 capture_output=True, text=True).stdout.strip()

    root = tmp_path / "afc"
    (root / "extras").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "extras" / "AFC.py").write_text(
        "self.gcode.register_command('CHANGE_TOOL', self.cmd)\n"
        "self.gcode.register_command('AFC_CUT', self.cmd)\n")
    macro_dir = root / "templates" / "indx_macros"
    (macro_dir / ".upstream").mkdir(parents=True)
    (macro_dir / "indx-tc-macros.cfg").write_text(PATCHED_TC)
    (macro_dir / "indx.cfg").write_text(BASE_USER)
    (macro_dir / ".upstream" / "indx-tc-macros.cfg").write_text(BASE_TC)
    (macro_dir / ".upstream" / "indx.cfg").write_text(BASE_USER)
    resync.write_upstream_record(macro_dir, "https://example.invalid/INDX", base_commit)
    return indx, root, macro_dir


def _commit(indx, name, text, msg="change"):
    (indx / "macros" / name).write_text(text)
    _git(indx, "add", ".")
    _git(indx, "commit", "-qm", msg)


def _run(env_, *args):
    indx, root, macro_dir = env_
    return resync.main(["--macro-dir", str(macro_dir), "--root", str(root), *args])


def test_check_passes_on_patched_tree(env, capsys):
    assert _run(env, "check") == 0
    assert "OK" in capsys.readouterr().out


def test_already_up_to_date(env, capsys):
    indx, _, _ = env
    assert _run(env, "merge", str(indx), "HEAD") == 0
    assert "nothing to merge" in capsys.readouterr().out


def test_clean_merge_updates_base(env, capsys):
    indx, _, macro_dir = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC.replace('RESPOND MSG="independent"',
                                                        'RESPOND MSG="changed upstream"'))
    assert _run(env, "merge", str(indx), "HEAD") == 0
    merged = (macro_dir / "indx-tc-macros.cfg").read_text()
    assert 'RESPOND MSG="changed upstream"' in merged
    assert "INDX_CHANGE_TOOL TOOL=0" in merged
    assert "changed upstream" in (macro_dir / ".upstream" / "indx-tc-macros.cfg").read_text()
    head = subprocess.run(["git", "-C", str(indx), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    assert resync.read_upstream_record(macro_dir)["commit"] == head


def test_conflict_leaves_markers_and_keeps_base(env, capsys):
    indx, _, macro_dir = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC.replace("CHANGE_TOOL TOOL=0",
                                                        "CHANGE_TOOL TOOL=0 TEMP=0"))
    assert _run(env, "merge", str(indx), "HEAD") == 1
    out = capsys.readouterr().out
    assert "CONFLICT indx-tc-macros.cfg" in out
    assert "<<<<<<<" in (macro_dir / "indx-tc-macros.cfg").read_text()
    assert "TEMP=0" not in (macro_dir / ".upstream" / "indx-tc-macros.cfg").read_text()


def test_accept_after_manual_fix(env, capsys):
    indx, _, macro_dir = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC.replace("CHANGE_TOOL TOOL=0",
                                                        "CHANGE_TOOL TOOL=0 TEMP=0"))
    _run(env, "merge", str(indx), "HEAD")
    (macro_dir / "indx-tc-macros.cfg").write_text(
        PATCHED_TC.replace("INDX_CHANGE_TOOL TOOL=0", "INDX_CHANGE_TOOL TOOL=0 TEMP=0"))
    assert _run(env, "accept", str(indx), "HEAD") == 0
    assert "TEMP=0" in (macro_dir / ".upstream" / "indx-tc-macros.cfg").read_text()


def test_lost_marker_detected(env, capsys):
    indx, _, macro_dir = env
    # Upstream deletes the latch macro the AFC patch lived in
    upstream = BASE_TC.replace("""[gcode_macro _TC_LATCH_MOVE]
gcode:
    {% set e = params.E|float %}
    INDX_EXTRUDER_MOVE DISTANCE={e}

""", "")
    _commit(indx, "indx-tc-macros.cfg", upstream)
    latch_patch = "    # AFC-PATCH: unsync lanes\n    AFC_INDX_PRE_LATCH\n"
    (macro_dir / "indx-tc-macros.cfg").write_text(PATCHED_TC.replace(latch_patch, ""))
    (macro_dir / ".upstream" / "indx-tc-macros.cfg").write_text(BASE_TC)
    markers = {"indx-tc-macros.cfg": 3, "indx.cfg": 0}
    report = resync.run_checks(macro_dir, env[1], markers)
    assert any("AFC-PATCH marker(s) lost" in e for e in report.errors)
    assert any("_TC_LATCH_MOVE no longer calls" in e for e in report.errors)


def test_new_collision_detected(env, capsys):
    indx, _, _ = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC + """
[gcode_macro AFC_CUT]
gcode:
    RESPOND MSG="cut"
""")
    assert _run(env, "merge", str(indx), "HEAD") == 1
    assert "[gcode_macro AFC_CUT] collides" in capsys.readouterr().out


def test_new_change_tool_caller_detected(env, capsys):
    indx, _, _ = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC + """
[gcode_macro NEW_THING]
gcode:
    CHANGE_TOOL TOOL=1
""")
    assert _run(env, "merge", str(indx), "HEAD") == 1
    assert "[gcode_macro NEW_THING] calls CHANGE_TOOL" in capsys.readouterr().out


def test_direct_active_tool_write_detected(env, capsys):
    indx, _, _ = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC + """
[gcode_macro SNEAKY]
gcode:
    SAVE_VARIABLE VARIABLE=active_tool VALUE=-1
""")
    assert _run(env, "merge", str(indx), "HEAD") == 1
    assert "[gcode_macro SNEAKY] writes active_tool directly" in capsys.readouterr().out


def test_comments_mentioning_change_tool_ok(env, capsys):
    indx, _, _ = env
    _commit(indx, "indx-tc-macros.cfg", BASE_TC.replace(
        "    G4 P10\n", "    G4 P10\n    # CHANGE_TOOL TOOL=1 would also work\n"))
    assert _run(env, "merge", str(indx), "HEAD") == 0


def test_new_user_variable_reported(env, capsys):
    indx, _, _ = env
    _commit(indx, "indx.cfg", BASE_USER.replace("variable_z_hop: 1.0",
                                                "variable_z_hop: 1.0\nvariable_new_knob: 3"))
    assert _run(env, "merge", str(indx), "HEAD") == 0
    assert "new user setting variable_new_knob" in capsys.readouterr().out


def test_new_upstream_file_reported(env, capsys):
    indx, _, macro_dir = env
    _commit(indx, "extra.cfg", "[gcode_macro EXTRA]\ngcode:\n")
    assert _run(env, "merge", str(indx), "HEAD") == 0
    assert "extra.cfg: new upstream file" in capsys.readouterr().out
    assert not (macro_dir / "extra.cfg").exists()


def test_vendored_tree_passes_checks():
    """The real vendored macros in this repo must always pass the resync checks."""
    report = resync.run_checks(resync.MACRO_DIR, resync.ROOT)
    assert report.errors == []
