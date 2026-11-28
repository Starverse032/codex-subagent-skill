# codex-subagent v3.0.0

A universal local Codex task runner skill for **any agent harness** (Antigravity, Claude Code, Codex, WorkBuddy, Cursor, Gemini CLI, or plain shell workflows). Invoke the skill when you explicitly want local Codex to execute a task.

The runner uses the native `codex exec --json` CLI by default, runs on the Python standard library with zero mandatory third-party dependencies, accepts UTF-8 briefs via files or stdin, lets Codex reply in plain text, and returns a structured JSON receipt with objective change and verification evidence.

## What's New in v3.0.0

- **CLI-first, stdlib-only core**: Defaults to `codex exec --json` on system Python 3.10+. `openai-codex` SDK, `jsonschema`, and `Pillow` are optional and loaded lazily only when `--backend sdk`, `--schema`, or `--gen-image` is explicitly requested.
- **Plain-text replies + structured receipts**: Codex replies in natural language without JSON wrapping or forced `artifacts` boilerplate, keeping Codex Desktop threads readable while the runner outputs structured JSON receipts (`changed_files`, `diff_stat`, and `--task-spec` check results).
- **Inherits local Codex config**: Drops hardcoded default models; inherits the model and effort from `~/.codex/config.toml` unless overridden via `--model` / `--effort`, with effort-linked default timeouts.
- **Resilient Windows Store discovery & preflight**: Dynamically resolves the latest installed Windows Store `OpenAI.Codex` binary instead of pinning versioned paths, and validates CLI, state directory, and SQLite access before creating a run directory.
- **Centralized state, idempotency & queue safety**: Stores new runs under `<state-dir>/runs/` (default `~/.codex-subagent/runs/`), supports `--task-id` idempotent retries, retries active Desktop writer locks briefly before reporting `thread_busy`, and maintains a 10-slot cross-host queue.
- **Streamlined CLI & docs**: Adds `wait`, `list-threads`, `prune --dry-run`, `--add-dir`, `--state-dir`, and `--task-file -` (UTF-8 stdin); retires the legacy Antigravity-only coordinator layer in favor of a single-page universal `SKILL.md`.

## Requirements & Install

- **Python 3.10+** (standard library only for core execution).
- **Installed and authenticated Codex CLI** (`codex login` or Codex Desktop session).
- **Writable workspace and shared state directory** (use `--sandbox read-only` for inspection-only tasks).

Copy the `codex-subagent` folder into your host's skills directory (no `.venv` setup required for normal use):

- **Antigravity**: `~/.gemini/config/skills/codex-subagent` (or `<workspace>/.agents/skills/codex-subagent`)
- **Claude Code**: `~/.claude/skills/codex-subagent`
- **Generic / Project-local**: `<workspace>/.agents/skills/codex-subagent`

Verify your environment from the skill directory:

```text
python scripts/doctor.py
```

See [installation and migration](references/host-integration.md) for optional dependencies (`--sdk`, `--schema`, `--images`) and v2-to-v3 migration notes.

## Usage

Run from the skill directory (or pass the absolute path to `scripts/codex_run.py`):

```text
python scripts/codex_run.py run --workdir DIR --task "Review this workspace" --sandbox read-only
python scripts/codex_run.py start --workdir DIR --task-file BRIEF.md --task-id ID
python scripts/codex_run.py wait RUN_DIR --timeout 60
python scripts/codex_run.py status RUN_DIR
python scripts/codex_run.py cancel RUN_DIR
python scripts/codex_run.py list-threads --workdir DIR
python scripts/codex_run.py prune --dry-run
```

- Pass `--task-file -` to supply a multi-line UTF-8 brief via stdin.
- `run` and `wait` print a compact JSON receipt on `stdout` (`status`, `exit_code`, `thread_id`, `run_id`, `run_dir`, `duration_s`, `final_message`, `changed_files`, `errors`), while progress updates go to `stderr`. A `wait` timeout ends waiting and leaves the task running.
- Follow up using `--resume UUID` or `--workdir` + an explicit `--lane NAME` (omitting both starts a new thread).
- Use `--add-dir DIR` to grant additional directory access, and `--task-spec FILE` to run independent `checks` and `required_files` verification under host permissions.

## Development & Tests

From the skill directory:

```text
python -m unittest discover -s tests
python scripts/doctor.py
```

## References

- [SKILL.md](SKILL.md) — Single-page universal quick start
- [CLI Reference](references/cli-reference.md) — Subcommands, flags, presets, and exit codes
- [Delegation Brief](references/delegation-brief.md) — Writing effective self-contained briefs
- [Task Specs & Receipts](references/events-schema.md) — `--task-spec`, `result.json`, and objective change evidence
- [Host Integration & Migration](references/host-integration.md) — Multi-host placement, optional dependencies, and v2 migration
- [Image Pipeline](references/image-pipeline.md) — Optional image generation & verification
- [Troubleshooting](references/troubleshooting.md) — Common errors and recovery

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/Starverse032">
        <img src="https://avatars.githubusercontent.com/u/337628395?v=4&s=128" width="80" height="80" alt="Starverse032" /><br />
        <sub><b>Starverse032</b></sub>
      </a><br />
      <sub>Author &amp; Maintainer</sub>
    </td>
    <td align="center">
      <a href="https://github.com/apps/gemini-code-assist">
        <img src="https://avatars.githubusercontent.com/in/956858?v=4&s=128" width="80" height="80" alt="Gemini Code Assist" /><br />
        <sub><b>Gemini Code Assist</b></sub>
      </a><br />
      <sub>Architecture, Audit &amp; QA</sub>
    </td>
    <td align="center">
      <a href="https://github.com/codex">
        <img src="https://avatars.githubusercontent.com/u/267193182?v=4&s=128" width="80" height="80" alt="Codex" /><br />
        <sub><b>Codex</b></sub>
      </a><br />
      <sub>Core Implementation</sub>
    </td>
  </tr>
</table>
