# Troubleshooting

- **Missing Python dependencies:** run `python scripts/setup_runtime.py`. For an existing native CLI, use `--local-cli`. Setup is local to this folder.
- **Authentication/runtime startup failure:** run `doctor.py`, inspect stderr.log and use the selected Codex runtime's own login command. Do not attach credentials to a task.
- **Runtime changed after setup:** rerun `setup_runtime.py --local-cli` to refresh the explicitly recorded executable, or run normal setup to restore the pinned runtime.
- **Task still running:** query `status RUN`; the latest phase/item and events.jsonl distinguish activity from silence. Timeout is a total budget and includes checks.
- **Windows progress sharing violation:** progress snapshots are optional; a busy progress file can skip a refresh without failing the task. Key control/result JSON reads retry briefly, then report persistent errors explicitly.
- **Need to stop a task:** `cancel RUN`, then query until terminal. There is no process-name kill fallback. On Windows the supervisor uses a job object for its own process tree, assigned before the backend executes.
- **Lost supervisor:** inspect logs and project changes, then use `resume RUN`. Recovery verifies that the recorded owned job/group has ended, including process birth identity. If the original tree is active or ownership evidence is missing (including older lost runs), recovery is refused. Inspect and resolve that state before explicitly using `--resume UUID`; result state is not a rollback mechanism.
- **Windows job assignment fails:** the run fails clearly and stops its newly created backend; it does not weaken process ownership or kill unrelated processes. Host job restrictions may need a different environment.
- **Image exists but verification fails:** inspect errors for missing provenance, invalid decoding or count mismatch. Open it in the host. Never substitute an old reference as output.
- **Directory already exists:** use a new run directory. Resume creates one automatically and keeps the original run intact.
- **Acceptance command failed:** inspect check-NN.log. The Codex turn may be completed while verification_failed correctly reports a failed acceptance check.
- **Custom schema failed:** inspect execution.final_message and the original caller schema. The response-schema.json file is only a diagnostic copy; changing it cannot alter the schema snapshot used for final verification. Remote `$ref` retrieval is disabled; use self-contained schemas.

No retries, model downgrades, permission downgrades, generator substitutions or backend switches happen silently.
