# Runner interface

Use `python scripts/codex_run.py COMMAND` from the skill directory, or pass the absolute script path from elsewhere.

| Command | Result |
|---|---|
| `run --workdir DIR --task-file FILE` | Submit, wait and print a terminal JSON receipt. Omitting the command also runs synchronously. |
| `start --workdir DIR --task-file FILE` | Persist the input, enqueue and print a JSON receipt containing `run_id` and `run_dir`. |
| `wait RUN_DIR [--timeout N]` | Wait for a terminal receipt. The wait deadline is separate from the execution deadline. |
| `status RUN_DIR` | Inspect the current state and progress snapshot. |
| `cancel RUN_DIR` | Request cancellation of that run. Use `wait` or `status` to see the outcome. |
| `list-threads [--workdir DIR] [--limit N]` | List registered sessions, with a source and an unknown lock state where there is no reliable observation. |
| `prune [--dry-run] [--older-than-days N]` | Review or remove eligible old centralized run records. Start with `--dry-run`. |

Deliver the task with one of `--task`, `--task-file` or `--task-spec`. Use `--task-file -` to read UTF-8 stdin. Host automation should parse the JSON receipt and consume diagnostic text from stderr.

## Task identity and state

- **`--resume UUID`** selects a thread explicitly. **`--lane NAME`** reuses a registered thread for that workdir and lane. Explicit resume has priority. Tasks without either can start new threads.
- **`--task-id ID`** returns the existing submission for the same ID and identical request. A changed request with that ID is a conflict.
- **`--state-dir DIR`** takes precedence over `CODEX_SUBAGENT_STATE_DIR` and `~/.codex-subagent`. Use the same state directory across hosts that share the ten-slot queue. **`--run-root`** and **`--run-dir`** override new run placement independently. Pass an existing run directory to inspect legacy records.
- **`--queue-timeout N`** limits time waiting for a slot. **`--timeout N`** on a submission sets the execution and acceptance budget. When omitted, the budget follows the explicit effort or the default budget. Allow time for cancellation and process teardown after the deadline.

## Runtime controls

- **`--backend cli|sdk`** defaults to CLI. SDK is an optional dependency. The runner does not automatically switch backends after a turn might have started.
- **`--model`**, **`--effort`** and **`--preset`** provide explicit runtime overrides. With no override, model and effort come from the Codex runtime. `config/presets.json` lists the available convenience presets.
- **`--codex-path`** selects a native executable for a run. Use the local runtime config to persist an explicit selection.
- **`--sandbox`** defaults to `workspace-write`. The approval policy defaults to `never`. **`--add-dir DIR`** is repeatable and adds a root to the task's workspace-write permission scope. Only pass explicitly authorized directories.
- **`--ephemeral`** opts out of persisted Codex threads. **`--dry-run`** prints the execution plan without creating a run directory.

## Advanced modes

Read [task specs and receipts](events-schema.md) for `--task-spec` and `--schema`. Read [image generation](image-pipeline.md) for `--gen-image`, input attachments and image export. Historical project grouping and runtime setup are covered in [migration](host-integration.md).
