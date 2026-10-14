# Resyncing the vendored INDX macros

The INDX Klipper macros are vendored in `templates/indx_macros/` with AFC patches applied. Each
patch is marked with a `# AFC-PATCH:` comment. Two more things are kept alongside them:

- `templates/indx_macros/.upstream/` is the untouched upstream copy the patched files were made
  from. It is the merge base for the next resync.
- `templates/indx_macros/UPSTREAM` records the INDX repo URL and commit of that copy.

## The AFC patches

| File | Patch | Why |
|---|---|---|
| `indx-tc-macros.cfg` | `CHANGE_TOOL` renamed to `INDX_CHANGE_TOOL`, callers updated | AFC registers `CHANGE_TOOL` |
| `indx-tc-macros.cfg` | `M104`/`M109` macros removed | AFC owns `M104`/`M109` and applies the INDX heating rules |
| `indx-tc-macros.cfg` | `_INDX_SET_ACTIVE_TOOL` calls `AFC_INDX_TOOL_CHANGED` instead of setting `T<n>.active` | AFC syncs its state and sets `active` from its own T(n) map |
| `indx-tc-macros.cfg` | `_TC_LATCH_MOVE` calls `AFC_INDX_PRE_LATCH` | Latch moves drive the extruder, lane steppers must be unsynced |
| `indx-cal.cfg` | `CHANGE_TOOL` callers use `INDX_CHANGE_TOOL` | Rename above |
| `homing.cfg` | Error message names `INDX_CHANGE_TOOL` | Rename above |
| `indx.cfg` | `T0`-`T3` are thin wrappers (`variable_active` plus an error body) | AFC claims `T<n>` through `map`. The wrappers keep the Mainsail/Fluidd tool buttons |

## With the script

```shell
# Merge upstream changes (default ref origin/main) into the patched files
utilities/indx_resync.py merge ~/INDX

# Run only the checks, for example after editing the vendored files by hand
utilities/indx_resync.py check
```

`merge` reads the upstream files at the ref with `git show` and does not touch your INDX
checkout. It runs `git merge-file` for each file (patched copy, `.upstream` base, new upstream),
then checks the result:

- no unresolved conflict markers
- no `# AFC-PATCH:` marker was lost in the merge
- no INDX macro name collides with an AFC command, and none of them call `CHANGE_TOOL`
- nothing writes `active_tool` except `_INDX_SET_ACTIVE_TOOL`
- `_INDX_SET_ACTIVE_TOOL` and `_TC_LATCH_MOVE` still call the AFC hooks

It also lists settings that were added to or removed from upstream `indx.cfg`, and new or removed
upstream files.

If everything passes, `.upstream/` and `UPSTREAM` move to the new commit. If a file conflicts or a
check fails, conflict markers are left in the file and the base is not moved. Fix the files, then
record the new base:

```shell
utilities/indx_resync.py accept ~/INDX origin/main
```

## By hand

1. Note the base commit in `templates/indx_macros/UPSTREAM`, and pick the upstream commit to
   sync to.
2. See what changed upstream:
   `git -C ~/INDX diff <base>..<new> -- macros/*.cfg`
3. Apply those changes to the files in `templates/indx_macros/`, keeping every `# AFC-PATCH:`
   change from the table above. If upstream changed a line that carries a patch, apply the
   upstream change and then re-apply the patch.
4. Check the result:
   - `grep -c "AFC-PATCH" templates/indx_macros/*.cfg` matches the count before the sync, unless
     a patch was intentionally dropped
   - new `[gcode_macro ...]` names do not collide with AFC commands (`grep register_command
     extras/*.py`, `config/macros/*.cfg`). Rename any that do, with an `AFC-PATCH` comment.
   - `grep -n "CHANGE_TOOL" templates/indx_macros/*.cfg` shows only `INDX_CHANGE_TOOL`,
     `_CHANGE_TOOL_APPLY` and comments
   - `grep -n "VARIABLE=active_tool" templates/indx_macros/*.cfg` only matches inside
     `_INDX_SET_ACTIVE_TOOL`
   - new `variable_` settings in upstream `indx.cfg` are listed in the CHANGELOG, since users
     keep their own `indx.cfg`
5. Copy the new upstream files into `templates/indx_macros/.upstream/` and update the commit in
   `UPSTREAM`.
6. Run `utilities/indx_resync.py check` and the test suite. `tests/test_indx_resync.py` also
   checks the vendored tree.
