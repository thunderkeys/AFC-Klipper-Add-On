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


def _head(indx):
    return subprocess.run(["git", "-C", str(indx), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


# ── Report.ok ────────────────────────────────────────────────────────────────

class TestReportOk:
    def test_no_errors(self):
        assert resync.Report(notes=["n"]).ok() is True

    def test_with_errors(self):
        assert resync.Report(errors=["e"]).ok() is False


# ── read_upstream_record / write_upstream_record ─────────────────────────────

class TestReadUpstreamRecord:
    def test_missing_file(self, tmp_path):
        assert resync.read_upstream_record(tmp_path) == {}

    def test_parses_keys(self, tmp_path):
        (tmp_path / "UPSTREAM").write_text("# comment: ignored\nrepo: https://x/y\n"
                                           "no colon here\ncommit:  abc \n")
        assert resync.read_upstream_record(tmp_path) == {"repo": "https://x/y", "commit": "abc"}


class TestWriteUpstreamRecord:
    def test_writes_record(self, tmp_path):
        resync.write_upstream_record(tmp_path, "https://x/y", "abc123")
        assert (tmp_path / "UPSTREAM").read_text() == (
            "# Upstream INDX macros the files in this directory were vendored from.\n"
            "# Managed by utilities/indx_resync.py, see docs/INDX_macro_resync.md.\n"
            "repo: https://x/y\n"
            "commit: abc123\n"
            "path: macros\n")
        assert resync.read_upstream_record(tmp_path) == {
            "repo": "https://x/y", "commit": "abc123", "path": "macros"}


# ── git / export_upstream ────────────────────────────────────────────────────

class TestGit:
    def test_returns_stdout(self, env):
        indx, _, _ = env
        assert resync.git(indx, "rev-parse", "HEAD").strip() == _head(indx)

    def test_raises_on_failure(self, env):
        indx, _, _ = env
        with pytest.raises(subprocess.CalledProcessError):
            resync.git(indx, "rev-parse", "no-such-ref")


class TestExportUpstream:
    def test_exports_cfg_files_only(self, env, tmp_path):
        indx, _, _ = env
        _commit(indx, "README.md", "not a cfg")
        dest = tmp_path / "out"
        dest.mkdir()
        assert sorted(resync.export_upstream(indx, "HEAD", dest)) == ["indx-tc-macros.cfg",
                                                                       "indx.cfg"]
        assert (dest / "indx-tc-macros.cfg").read_text() == BASE_TC
        assert not (dest / "README.md").exists()


# ── count_markers ────────────────────────────────────────────────────────────

class TestCountMarkers:
    def test_counts_per_file(self, env):
        _, _, macro_dir = env
        assert resync.count_markers(macro_dir) == {"indx-tc-macros.cfg": 3, "indx.cfg": 0}


# ── afc_command_names ────────────────────────────────────────────────────────

class TestAfcCommandNames:
    def test_collects_registered_and_macro_names(self, tmp_path):
        (tmp_path / "extras").mkdir()
        (tmp_path / "config" / "macros").mkdir(parents=True)
        (tmp_path / "extras" / "a.py").write_text(
            "gcode.register_command('CMD_ONE', f)\n"
            "self.register_commands(self.show, \"CMD_TWO\", f, h)\n"
            "gcode.register_mux_command('CMD_MUX', 'LANE', name, f)\n"
            "register_command('lower', f)\n")
        (tmp_path / "config" / "macros" / "m.cfg").write_text(
            "[gcode_macro my_macro]\ngcode:\n[respond]\n[gcode_macro]\n")
        assert resync.afc_command_names(tmp_path) == {
            "CHANGE_TOOL", "M104", "M109", "CMD_ONE", "CMD_TWO", "CMD_MUX", "MY_MACRO"}


# ── macro_sections / user_variables ──────────────────────────────────────────

class TestMacroSections:
    def test_splits_macros(self):
        text = "# head\n[gcode_macro A]\nx\n[respond]\ny\n[gcode_macro B]\nz\n"
        assert resync.macro_sections(text) == [("A", ["x"]), ("B", ["z"])]


class TestUserVariables:
    def test_variable_names(self):
        assert resync.user_variables("variable_a: 1\n# variable_b: 2\nvariable_c : 3\n") == {
            "a", "c"}


# ── run_checks ───────────────────────────────────────────────────────────────

def _write_tc(macro_dir, text):
    (macro_dir / "indx-tc-macros.cfg").write_text(text)


class TestRunChecks:
    def test_patched_tree_passes(self, env):
        _, root, macro_dir = env
        report = resync.run_checks(macro_dir, root)
        assert report.errors == []
        assert report.notes == []

    def test_conflict_markers(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + "<<<<<<< AFC\nx\n>>>>>>> upstream\n")
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: unresolved conflict markers"]

    def test_lost_marker(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC.replace("    # AFC-PATCH: INDX_CHANGE_TOOL\n", ""))
        report = resync.run_checks(macro_dir, root, {"indx-tc-macros.cfg": 3})
        assert report.errors == ["indx-tc-macros.cfg: 1 AFC-PATCH marker(s) lost (3 -> 2), "
                                 "re-apply the dropped patch"]

    def test_markers_kept(self, env):
        _, root, macro_dir = env
        report = resync.run_checks(macro_dir, root, {"indx-tc-macros.cfg": 3, "indx.cfg": 0})
        assert report.errors == []

    def test_collision(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + "\n[gcode_macro AFC_CUT]\ngcode:\n")
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: [gcode_macro AFC_CUT] collides with an AFC command, rename it "
            "(and its callers) with an AFC-PATCH"]

    def test_t_wrappers_allowed(self, env):
        _, root, macro_dir = env
        (root / "extras" / "AFC.py").write_text("register_command('T0', f)\n")
        _write_tc(macro_dir, PATCHED_TC + "\n[gcode_macro T0]\ngcode:\n")
        assert resync.run_checks(macro_dir, root).errors == []

    def test_active_tool_writer(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + "\n[gcode_macro SNEAKY]\ngcode:\n"
                  "    SAVE_VARIABLE VARIABLE=active_tool VALUE=-1\n")
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: [gcode_macro SNEAKY] writes active_tool directly, route it "
            "through _INDX_SET_ACTIVE_TOOL so AFC is notified"]

    @pytest.mark.parametrize("line", ["    CHANGE_TOOL TOOL=1", "    CHANGE_TOOL",
                                      '    {% set c = "CHANGE_TOOL TOOL=%d" % t %}'])
    def test_change_tool_caller(self, env, line):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + f"\n[gcode_macro NEW]\ngcode:\n{line}\n")
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: [gcode_macro NEW] calls CHANGE_TOOL, use INDX_CHANGE_TOOL"]

    @pytest.mark.parametrize("line", ["    # CHANGE_TOOL TOOL=1", "", "    INDX_CHANGE_TOOL TOOL=1",
                                      "    _CHANGE_TOOL_APPLY TOOL=1"])
    def test_not_change_tool_callers(self, env, line):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + f"\n[gcode_macro NEW]\ngcode:\n{line}\n")
        assert resync.run_checks(macro_dir, root).errors == []

    def test_set_active_tool_hook_missing(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC.replace(
            "    AFC_INDX_TOOL_CHANGED TOOL={params.TOOL}\n", ""))
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: _INDX_SET_ACTIVE_TOOL no longer calls AFC_INDX_TOOL_CHANGED"]

    def test_latch_hook_missing(self, env):
        _, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC.replace("    AFC_INDX_PRE_LATCH\n", ""))
        assert resync.run_checks(macro_dir, root).errors == [
            "indx-tc-macros.cfg: _TC_LATCH_MOVE no longer calls AFC_INDX_PRE_LATCH"]

    def test_user_variable_notes(self, env, tmp_path):
        _, root, macro_dir = env
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "indx.cfg").write_text(BASE_USER.replace("variable_z_hop: 1.0",
                                                            "variable_new_knob: 3"))
        report = resync.run_checks(macro_dir, root, None, macro_dir / ".upstream", new_dir)
        assert report.notes == [
            "indx.cfg: new user setting variable_new_knob, mention it in the CHANGELOG, users "
            "must add it to their own indx.cfg",
            "indx.cfg: variable_z_hop removed upstream"]

    def test_notes_need_both_indx_cfg(self, env, tmp_path):
        """A missing new indx.cfg alone skips the notes."""
        _, root, macro_dir = env
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        report = resync.run_checks(macro_dir, root, None, macro_dir / ".upstream", new_dir)
        assert report.notes == []

    def test_notes_need_base_indx_cfg(self, env, tmp_path):
        """A missing base indx.cfg alone skips the notes."""
        _, root, macro_dir = env
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "indx.cfg").write_text("variable_x: 1\n")
        (macro_dir / ".upstream" / "indx.cfg").unlink()
        report = resync.run_checks(macro_dir, root, None, macro_dir / ".upstream", new_dir)
        assert report.notes == []

    def test_notes_need_new_dir(self, env):
        """No new_dir alone skips the notes."""
        _, root, macro_dir = env
        report = resync.run_checks(macro_dir, root, None, macro_dir / ".upstream", None)
        assert report.notes == []

    def test_notes_need_base_dir(self, env, tmp_path):
        """No base_dir alone skips the notes."""
        _, root, macro_dir = env
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "indx.cfg").write_text("variable_x: 1\n")
        report = resync.run_checks(macro_dir, root, None, None, new_dir)
        assert report.notes == []


# ── print_report ─────────────────────────────────────────────────────────────

class TestPrintReport:
    def test_notes_then_errors(self, capsys):
        resync.print_report(resync.Report(errors=["e1"], notes=["n1", "n2"]))
        assert capsys.readouterr().out == "NOTE  n1\nNOTE  n2\nERROR e1\n"


# ── accept ───────────────────────────────────────────────────────────────────

class TestAccept:
    def test_replaces_base_and_record(self, env, tmp_path):
        _, _, macro_dir = env
        (macro_dir / ".upstream" / "old.cfg").write_text("stale")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        (new_dir / "indx.cfg").write_text("new")
        resync.accept(macro_dir, new_dir, "https://x/y", "def456")
        assert sorted(p.name for p in (macro_dir / ".upstream").iterdir()) == ["indx.cfg"]
        assert (macro_dir / ".upstream" / "indx.cfg").read_text() == "new"
        assert resync.read_upstream_record(macro_dir)["commit"] == "def456"


# ── cmd_merge ────────────────────────────────────────────────────────────────

class TestCmdMerge:
    def test_already_up_to_date(self, env, capsys):
        indx, root, macro_dir = env
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 0
        assert capsys.readouterr().out == f"Already at {_head(indx)}, nothing to merge.\n"

    def test_clean_merge(self, env, capsys):
        indx, root, macro_dir = env
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC.replace('MSG="independent"', 'MSG="changed upstream"'))
        head = _head(indx)
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 0
        assert capsys.readouterr().out == (
            "MERGED indx-tc-macros.cfg\n"
            "MERGED indx.cfg\n"
            f"\nResynced to {head}. Review `git diff templates/indx_macros` and add a "
            "CHANGELOG entry.\n")
        merged = (macro_dir / "indx-tc-macros.cfg").read_text()
        assert 'MSG="changed upstream"' in merged
        assert "INDX_CHANGE_TOOL TOOL=0" in merged
        assert resync.read_upstream_record(macro_dir) == {
            "repo": "https://example.invalid/INDX", "commit": head, "path": "macros"}

    def test_repo_from_git_when_record_has_none(self, env):
        indx, root, macro_dir = env
        (macro_dir / "UPSTREAM").write_text("commit: 0000\n")
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC.replace('MSG="independent"', 'MSG="changed upstream"'))
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 0
        assert resync.read_upstream_record(macro_dir)["repo"] == "https://example.invalid/INDX"

    def test_conflict(self, env, capsys):
        indx, root, macro_dir = env
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC.replace("CHANGE_TOOL TOOL=0", "CHANGE_TOOL TOOL=0 TEMP=0"))
        head = _head(indx)
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 1
        out = capsys.readouterr().out
        assert out.startswith("CONFLICT indx-tc-macros.cfg: 1 conflict(s)\nMERGED indx.cfg\n")
        assert out.endswith(f"\nResync to {head[:12]} incomplete. Fix the above, then run:\n"
                            f"  utilities/indx_resync.py accept {indx} HEAD\n")
        assert "<<<<<<<" in (macro_dir / "indx-tc-macros.cfg").read_text()
        assert "TEMP=0" not in (macro_dir / ".upstream" / "indx-tc-macros.cfg").read_text()

    def test_checks_fail_without_conflict(self, env, capsys):
        indx, root, macro_dir = env
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC + "\n[gcode_macro NEW]\ngcode:\n    CHANGE_TOOL TOOL=1\n")
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 1
        out = capsys.readouterr().out
        assert ("ERROR indx-tc-macros.cfg: [gcode_macro NEW] calls CHANGE_TOOL, use "
                "INDX_CHANGE_TOOL\n") in out
        assert "MERGED indx-tc-macros.cfg\n" in out
        assert resync.read_upstream_record(macro_dir)["commit"] != _head(indx)

    def test_new_and_removed_upstream_files(self, env, capsys):
        indx, root, macro_dir = env
        (indx / "macros" / "indx.cfg").unlink()
        _commit(indx, "extra.cfg", "[gcode_macro EXTRA]\ngcode:\n")
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 0
        out = capsys.readouterr().out
        assert out.startswith(
            "NOTE  extra.cfg: new upstream file, not vendored. Add it by hand if AFC needs it.\n"
            "NOTE  indx.cfg: removed upstream, decide whether to drop the vendored copy.\n"
            "MERGED indx-tc-macros.cfg\n")
        assert not (macro_dir / "extra.cfg").exists()

    @pytest.mark.parametrize("code", [-9, 255])
    def test_merge_file_failure(self, env, capsys, monkeypatch, code):
        indx, root, macro_dir = env
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC.replace('MSG="independent"', 'MSG="changed upstream"'))
        real_run = subprocess.run

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "merge-file"]:
                return subprocess.CompletedProcess(args, code, "", "boom\n")
            return real_run(args, **kwargs)
        monkeypatch.setattr(resync.subprocess, "run", fake_run)
        assert resync.cmd_merge(indx, "HEAD", macro_dir, root) == 2
        assert capsys.readouterr().out == (
            "ERROR indx-tc-macros.cfg: git merge-file failed: boom\n")


# ── cmd_accept ───────────────────────────────────────────────────────────────

class TestCmdAccept:
    def test_records_after_manual_fix(self, env, capsys):
        indx, root, macro_dir = env
        _commit(indx, "indx-tc-macros.cfg",
                BASE_TC.replace("CHANGE_TOOL TOOL=0", "CHANGE_TOOL TOOL=0 TEMP=0"))
        _write_tc(macro_dir, PATCHED_TC.replace("INDX_CHANGE_TOOL TOOL=0",
                                                "INDX_CHANGE_TOOL TOOL=0 TEMP=0"))
        head = _head(indx)
        assert resync.cmd_accept(indx, "HEAD", macro_dir, root) == 0
        assert capsys.readouterr().out == f"Recorded {head} as the vendored INDX base.\n"
        assert "TEMP=0" in (macro_dir / ".upstream" / "indx-tc-macros.cfg").read_text()

    def test_refuses_failing_tree(self, env, capsys):
        indx, root, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + "<<<<<<< AFC\n")
        assert resync.cmd_accept(indx, "HEAD", macro_dir, root) == 1
        assert capsys.readouterr().out == "ERROR indx-tc-macros.cfg: unresolved conflict markers\n"

    def test_repo_from_git_when_record_has_none(self, env):
        indx, root, macro_dir = env
        (macro_dir / "UPSTREAM").write_text("commit: 0000\n")
        assert resync.cmd_accept(indx, "HEAD", macro_dir, root) == 0
        assert resync.read_upstream_record(macro_dir)["repo"] == "https://example.invalid/INDX"


# ── main ─────────────────────────────────────────────────────────────────────

class TestMain:
    def test_check_ok(self, env, capsys):
        assert _run(env, "check") == 0
        assert capsys.readouterr().out == "OK\n"

    def test_check_failed(self, env, capsys):
        _, _, macro_dir = env
        _write_tc(macro_dir, PATCHED_TC + "<<<<<<< AFC\n")
        assert _run(env, "check") == 1
        assert capsys.readouterr().out == (
            "ERROR indx-tc-macros.cfg: unresolved conflict markers\nFAILED\n")

    def test_merge(self, env, capsys):
        indx, _, _ = env
        assert _run(env, "merge", str(indx), "HEAD") == 0
        assert capsys.readouterr().out == f"Already at {_head(indx)}, nothing to merge.\n"

    def test_accept(self, env, capsys):
        indx, _, _ = env
        assert _run(env, "accept", str(indx), "HEAD") == 0
        assert capsys.readouterr().out == f"Recorded {_head(indx)} as the vendored INDX base.\n"

    def test_main_guard(self, env, capsys, monkeypatch):
        import runpy
        _, root, macro_dir = env
        script = Path(resync.__file__)
        monkeypatch.setattr(sys, "argv", [str(script), "--macro-dir", str(macro_dir),
                                          "--root", str(root), "check"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(script), run_name="__main__")
        assert exc.value.code == 0
        assert capsys.readouterr().out == "OK\n"


# ── The vendored tree in this repo ───────────────────────────────────────────

class TestVendoredTree:
    def test_passes_checks(self):
        """The real vendored macros must always pass the resync checks."""
        report = resync.run_checks(resync.MACRO_DIR, resync.ROOT)
        assert report.errors == []
