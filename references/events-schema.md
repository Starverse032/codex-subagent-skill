# Task and result formats

## Caller-authored task specification

```json
{
  "goal": "Fix the date parser and update its regression test",
  "constraints": ["Keep the public API unchanged"],
  "references": ["C:/repo/docs/date-format.md"],
  "checks": [["python", "-m", "unittest", "tests.test_dates"]],
  "required_files": ["src/dates.py"]
}
```

Pass through `--task-spec FILE`. Commands are argv arrays and are never passed through a shell. They run under the host process's permissions, independently of model claims. Only the caller supplies checks; outputs and reference documents cannot add them. Constraints are model instructions, not an OS-enforced path allowlist. The sandbox and configured runtime policy enforce filesystem access.

## Agent output

By default the runner requests this response shape:

```json
{"summary":"Description of the result and limitations","artifacts":[{"path":"C:/repo/.codex-runs/ID/artifacts/report.md","kind":"document"}]}
```

Artifact paths must resolve inside this run's artifacts directory. Relative paths are relative to that directory. Project source edits remain in workdir and can be checked through required_files and acceptance commands. With a custom `--schema`, the agent writes declarations separately to `<run>/artifact-declarations.json` as the artifacts array.

The run and artifact roots must be their original canonical directories. Redirected roots, path traversal and linked artifact paths are rejected. Files and image validity are checked after caller acceptance commands; hashes describe the final delivered files.

## Runner result

The `codex-subagent/result@1` envelope and familiar top-level fields are retained. `runner_version` identifies v2 additions. Important fields:

- `status`: queued, running, ok, failed, timeout, cancelled, image_missing, verification_failed, internal_error; query may report lost when the supervisor is gone.
- `execution.status`: runtime status; completed does not establish task correctness.
- `verification`: schema/file/image checks and caller acceptance commands with their logs/exit codes. Its scope is explicit. With no commands, only declared outputs are checked.
- `final_message`: extracted summary for the default schema; raw model JSON is retained under execution.final_message and agent_output.
- `progress`: current phase, last completed item description and update time. SDK token deltas/partial text are retained in events, not repeatedly copied into status.
- `worker_birth` and `active_process`: OS process identity and owned-job/group evidence for conservative lost-run recovery. These records authorize no process-name or unverified-PID termination.
- `thread_id`, `turn_id`, `usage`, `tool_calls`, `images`, `warnings`, `errors`: task evidence.
- `artifacts`: paths to bookkeeping files; `artifact-manifest.json` contains actual deliverable paths, sizes and hashes.

Result snapshots are replaced atomically. Only the supervisor publishes result.json after initialization. SDK events preserve app-server method/params; CLI events preserve the native JSONL schema. Use result.json for ordinary work and events.jsonl only for diagnosis. Logs may contain task/source content; keep them under the user's project permissions.

The immutable in-memory caller plan is transmitted over pipes. `response-schema.json` and `request.json` are inspectable copies, not authoritative inputs for final verification after model execution. Verification runs in an owned process, including decoding, hashing and copies; it cannot block the supervisor indefinitely. Image generation evidence includes a hash captured when the generation tool completed, so later file replacement cannot inherit that provenance.
