# codex-subagent v2.2.1

A local Codex task runner with a thin skill entry point, an official Python SDK adapter, owned process supervision and independent acceptance checks.

Invoke the skill by asking to use a Codex subagent or typing the host's skill command. The main Gemini divides the scope and assigns it to Gemini coordinator subagents. Each coordinator prepares the [delegation brief](references/delegation-brief.md), dispatches a Codex worker and checks the result. The main Gemini verifies the combined delivery. Related turns share a conversation identity and reuse Codex sessions. A shared local queue limits simultaneous runs to ten.

## Install

For Antigravity Desktop, copy the `codex-subagent` folder into `~/.gemini/config/skills/` or the workspace's `.agents/skills/`. For Claude Code, use `~/.claude/skills/`. Remove any previous codex-subagent routing block that automatically dispatches backend tasks from Claude's `CLAUDE.md`. In the skill folder run:

```powershell
python scripts/setup_runtime.py
python scripts/doctor.py
```

For the Antigravity coordinator, install [codex-lane-coordinator.md](assets/antigravity/codex-lane-coordinator.md) in `~/.gemini/config/agents/` or the workspace's `.agents/agents/`.

Requires Python 3.10+ and Codex authentication. Dependencies are installed in a private `.venv`; no global Python packages are changed. Normal setup installs the SDK's pinned runtime. `python scripts/setup_runtime.py --local-cli` instead uses an existing native executable, records its path in `config/runtime.local.json`, and installs the SDK library without its large bundled runtime dependency. This is an intentional local-runtime mode; `pip check` will report that omitted runtime distribution. The configured CLI supplies it operationally.

## Usage

```powershell
python scripts/codex_run.py --task "Review the project" --workdir "C:\repo" --sandbox read-only
python scripts/codex_run.py start --task-file "C:\repo\task.md" --workdir "C:\repo"
python scripts/codex_run.py status "<run>"
python scripts/codex_run.py result "<run>"
python scripts/codex_run.py cancel "<run>"
python scripts/codex_run.py resume "<run>" --task "Apply this follow-up"
```

Omitting the subcommand performs a synchronous run, preserving the old entry point. Run/start/resume print one run path. Status/result/cancel print JSON. Cancellation is requested first; query status for completion.

Grouped tasks use the SDK, GPT-6.2 Sol, medium reasoning, workspace-write and approvals never by default. The runner creates a named local App Server project and associates threads automatically. Raise effort to high or xhigh for complex work. Queue wait has a separate timeout from the 900-second default execution and verification budget. CLI remains available for ungrouped runs. See the [host integration guide](references/host-integration.md) for Desktop sidebar visibility.

Automatic reuse selects the latest completed thread for the same conversation, workspace and lane. Manual resume keeps the previous model and effort by default. Use the official model documentation and authenticated runtime catalog to verify a model upgrade.

Every run owns a fresh `<workdir>/.codex-runs/<timestamp>-<id>/`. Ignore `.codex-runs/` and `.venv/` in your repository. Source edits belong to the project; exported deliverables belong to `<run>/artifacts/`. Old directories and export destinations are never cleared.

The supervisor sends the original task plan and schema through pipes to its workers. Changing a diagnostic schema file cannot weaken final validation. Caller acceptance commands finish before final file/image validation and hashes. Both runtime and verification processes are subject to the total deadline. Artifact roots and paths must remain canonical; redirected roots and symlink/junction paths are rejected. Generation filename collisions retain both files under distinct names.

`lost` recovery checks process birth identity and the owned job/process group before continuing. It never kills a saved PID. An active tree or missing ownership evidence prevents automatic recovery. On Windows a named job gives crash teardown evidence; on POSIX process groups must already be gone. The release's runtime tests cover Windows.

## What is verified

`result.json` retains the v1 top-level fields and adds execution, verification, progress and artifact evidence. Final model output is structured; local checks validate its schema and declared deliverables. Caller-authored acceptance commands have independent logs and exit codes. They execute under host permissions, so supply them only as part of authorized work. Image checks decode files, require the exact requested count and match hashes to completed image-generation tool output. Host visual inspection remains required.

The SDK adapter uses the public typed `CodexClient` API from SDK 0.163.0, with an explicit handler declining unexpected client approval requests. Its default permissive handler is not used.

## Tests

```powershell
python -B -m unittest discover -s tests -v
```

Tests use an isolated SDK fixture for model-free lifecycle tests, while real child processes exercise Windows process ownership, cancellation and timeout. Test fixtures are enabled only through the test subprocess's PYTHONPATH. See the release validation report for actual live SDK coverage.

See [SKILL.md](SKILL.md) for the host workflow and `references/` for schemas, options and migration details. [Official SDK documentation](https://learn.chatgpt.com/docs/codex-sdk).
