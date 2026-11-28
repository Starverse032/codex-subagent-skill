# Installation and migration

## Install in an agent host

Place the skill folder in the location read by your host. For Antigravity, use the host's user or workspace skill directory. When invoking from a shell, call `scripts/codex_run.py` directly with an available Python 3.10+ interpreter. Run `python scripts/doctor.py` from the skill directory to check CLI discovery.

Every host follows the same command workflow. To share the queue, use the same accessible local state directory across hosts. If a host cannot access the default state directory, select a shared path allowed by that host and pass it as `--state-dir DIR` or set `CODEX_SUBAGENT_STATE_DIR`.

An optional main-agent instruction template is provided at [assets/AGENTS.md](../assets/AGENTS.md). Keep it in `assets/` (rather than the skill root) so Codex subagents do not auto-load main-agent role instructions; copy or merge it only into your host's main-agent configuration.

## Optional dependencies

Default CLI tasks run without third-party Python packages. To install optional features from this skill folder, choose the flags you need:

```text
python scripts/setup_runtime.py --sdk --schema --image
```

`--sdk` prepares the optional SDK backend, `--schema` installs the custom-schema validator, and `--image` installs prerequisites for strict image verification. Run the task with the Python interpreter printed by setup, or use an existing Python environment with those dependencies. On Windows the private interpreter is `.venv/Scripts/python.exe`. On Unix it is `.venv/bin/python`.

The default runtime discovers a native Codex CLI, including installed Windows Store builds. Use `python scripts/setup_runtime.py --local-cli` to record automatic discovery or add an executable path to record a fixed selection. Codex manages its own authentication.

## Upgrade from v2

1. Stop v2 submissions from every host using the same state directory. Wait for existing workers to finish and keep a copy of the v2 skill for rollback.
2. Run v3 against the same state directory. The queue migration takes a SQLite backup before upgrading an existing database. The migration preserves existing project and thread records. Use `list-threads --workdir DIR` to find a legacy thread, then pass `--resume UUID --lane NAME` to establish an explicit v3 lane association. Legacy conversation lanes can share a workdir, so they are preserved without automatic reassignment.
3. Update callers to parse JSON stdout and use the receipt's `run_dir`. New records default to `<state-dir>/runs/`. Pass the exact directory to inspect earlier `workdir/.codex-runs/` records.
4. Remove any mandatory coordinator step from host workflows. The v3 skill does not install or update the Antigravity coordinator. If `doctor.py` detects a previously installed definition, review its host references before retiring it.
5. Run the doctor and inspect the new records before rolling out to other hosts. Keep both versions idle while restoring the old state. Restore the backed-up SQLite database only after all v3 workers have stopped.

## Projects and Desktop threads

Direct CLI runs use the explicitly supplied workdir. Desktop may associate a saved thread with a project by its workspace. Verify in the Desktop interface before asserting sidebar placement. Use `list-threads` to discover threads known to this runner and `--resume UUID` to select one. A reported Desktop write conflict can require ending the writer in Desktop before resuming.

Existing v2 project mappings remain available for compatibility. The old `--host`, `--conversation` and `--project-title` grouping is an advanced SDK mode whose project support depends on the local Codex runtime. Use direct dispatch for new workflows.
