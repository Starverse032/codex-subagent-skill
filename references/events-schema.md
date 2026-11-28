# Task specs and receipts

## Caller-authored acceptance

Save a UTF-8 JSON file and pass it as `--task-spec FILE`.

```json
{
  "goal": "Fix the date parser and update its regression test",
  "constraints": ["Preserve the public API"],
  "references": ["C:/repo/docs/date-format.md"],
  "checks": [["python", "-m", "unittest", "tests.test_dates"]],
  "required_files": ["src/dates.py"]
}
```

`checks` accepts argument arrays. These commands run directly with host process permissions. Choose them from the authorized task. The runner checks required files inside the workdir. The sandbox enforces filesystem permissions independently of task prose.

## Model reply and exports

The default reply is plain text. Caller-authored checks and required files still apply. Use `--schema FILE` when the task explicitly requires JSON conforming to a caller-owned schema. This mode loads `jsonschema`; use a self-contained schema because remote references are disabled. With a custom schema, export declarations go in `<run_dir>/artifact-declarations.json` as an array of objects containing `path` and `kind`.

`--gen-image` enables the image output contract described in [image generation](image-pipeline.md). Relative export paths are interpreted inside the run's `artifacts/` directory. The verifier checks declared files after acceptance commands and rejects redirected roots, path traversal and linked export paths.

## Process and verification records

`run` and `wait` print JSON with `status`, `exit_code`, `thread_id`, `run_id`, `run_dir`, `duration_s`, `final_message`, `changed_files`, `diff_stat`, `errors` and a verification summary. The full `result.json` includes execution and process evidence.

- Inspect `execution.status` and `verification` independently. Successful execution and machine checks cover the checks configured for the task. Assess the full user request before reporting completion.
- Use `changed_files` and `diff_stat` as evidence of observed changes. Git comparisons can include other edits made during the run. Event coverage in non-Git directories depends on the backend and may omit changes made by commands.
- Compare `last_event_at` with `runner_heartbeat_at`. A current heartbeat indicates a live supervisor. Recent events show backend activity.
- Inspect `events.jsonl`, `stderr.log` and `check-NN.log` only when needed. Keep logs under the same access controls as the task and source files.

Cancellation and timeouts can leave partial edits. Check the recorded thread and files before submitting a replacement task.
