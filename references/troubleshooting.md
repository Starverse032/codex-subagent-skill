# Troubleshooting

- **CLI missing or stale.** Run `python scripts/doctor.py`. Automatic discovery can find an installed native Windows Store CLI. Correct a fixed executable with `setup_runtime.py --local-cli PATH` or switch to discovery with `setup_runtime.py --local-cli`.
- **Authentication failed.** Inspect the run's diagnostics and use the selected native CLI's login flow. Keep credentials out of task files.
- **State directory denied.** Use a shared directory the invoking host can write. Set `--state-dir DIR` or `CODEX_SUBAGENT_STATE_DIR`. Use the same state location across participating hosts to preserve the shared queue.
- **Optional mode unavailable.** Use the relevant [optional dependencies](host-integration.md#optional-dependencies) and invoke the runner with their Python interpreter.
- **Run or wait timed out.** Query `status RUN_DIR`. The `wait` timeout is local to that command. An execution timeout initiates cancellation and bounded process teardown. Check the latest recorded event and any partial result.
- **Active thread writer.** `thread_busy` identifies the unavailable thread. Let the other writer finish or close its active session in Codex Desktop, then retry intentionally. File changes are not rolled back.
- **Lost supervisor.** Inspect recorded process ownership and observed changes. Resolve the original run before resuming its thread or submitting another task.
- **Need to stop a task.** Run `cancel RUN_DIR` followed by `wait RUN_DIR`. The supervisor attempts graceful shutdown before forcibly ending its owned process tree when necessary.
- **Acceptance check failed.** Inspect the check log and the recorded exit code. Address the specific failure in the same thread when appropriate.
- **Image validation failed.** Inspect the reported format, count or provenance failure. Open the image in the host to assess the content.
- **Custom schema validation failed.** Compare the model reply to the original caller schema. The run directory's `response-schema.json` is an inspectable copy. Use a self-contained schema.
