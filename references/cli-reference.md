# Runner interface

`python scripts/codex_run.py [run|start] --task-file FILE --workdir ABSOLUTE_DIR`

Use `status RUN`, `result RUN`, `cancel RUN`, or `resume RUN --task-file FILE` to manage a task. The path returned by run/start/resume is the task handle. No daemon, network listener or MCP service is required.

| Option | Behavior |
|---|---|
| `--task`, `--task-file`, `--task-spec` | Mutually exclusive; `--task-file -` consumes caller stdin |
| `--backend sdk|cli` | SDK default; no automatic backend fallback |
| `--preset` | `standard` default: verified Sol/medium; `deep`: same Sol/high; `max`: same Sol/max; explicit `inherit`, `fast` (Sol/low), `image` (Sol/medium + generation) |
| `--model`, `--effort` | Explicit overrides; raise effort before changing models; no model snapshot allowlist or silent downgrade |
| `--sandbox` | read-only or workspace-write; unrestricted mode requires the danger pair |
| `--approval` | never or approve-for-me; human on-request mode is unsupported |
| `--timeout` | 1–3600 seconds, covering runtime startup, execution and checks; up to 3 seconds of cancellation grace plus process teardown |
| `--run-root` | Defaults to workdir/.codex-runs |
| `--run-dir` | Exact NEW directory; existing directories are rejected |
| `--schema` | Custom final JSON Schema; local validation is performed with remote schema retrieval disabled |
| `--image` | Input attachment, repeatable |
| `--gen-image`, `--image-count` | Generation contract; count 1–8 |
| `--image-size`, `--image-format` | Composition intent; image size differences are warnings; decoding requires PNG/JPEG |
| `--image-dir` | Optional additive export; generation itself uses run/artifacts; no existing files are removed |
| `--config` | Raw runtime config; permission/approval/profile overrides are rejected |
| `--resume UUID` | Explicit session only, in a fresh run directory |
| `--ephemeral` | No persisted Codex thread; cannot resume |
| `--codex-path` | Explicit native binary; otherwise use configured local override or SDK runtime |
| `--require-git-repo` | Retains the CLI backend's Git-repository check; SDK behavior is runtime-owned |
| `--dry-run` | Prints a JSON execution plan and creates no run directory |
| `--host`, `--conversation`, `--project-title` | All three together enable an isolated originating-chat group and automatic thread reuse |
| `--lane` | Independent workstream, default `main`. A busy lane queues additional tasks |
| `--fresh-session` | Start a new thread in the same group and lane |
| `--queue-timeout` | Queue-only timeout in seconds, default 3600, maximum 86400 |
| `--codex-project-id` | Bind a verified App Server project ID and reject conflicting bindings |

`project --host HOST --conversation ID --project-title TITLE --workdir DIR` returns the local group, App Server project binding, runs and reusable sessions. The field `desktop_id` is the persisted App Server ID. Grouped tasks require the SDK backend because it can create a project and submit a thread with `projectId`. See [host integration](host-integration.md) for Desktop sidebar visibility.

The shared SQLite queue lives in `~/.codex-subagent/dispatch-v1.sqlite3`. Keep the same Windows user and state directory across clients. `CODEX_SUBAGENT_STATE_DIR` is an override for isolated tests or a single centrally configured path. Queued runs can be cancelled. The execution budget begins when admitted. The ten-slot limit covers SDK and CLI runs started through this runner on this user account and machine. Grouped runs accept only medium, high and xhigh. Scheduling and reuse reduce concurrency and repeated setup.

`--slug` and `--allow-unknown-model` remain accepted for caller compatibility but are no-ops. Unique run IDs replace task text in directory names. Unknown models are validated by the actual runtime.

CLI prompts go through stdin, not command-line arguments. For automatic review, the CLI adapter omits the incompatible sandbox flag. Resume routes global automatic-review options before `exec`.

In v2.1, new runs read the default preset from `config/presets.json` (`standard`: verified Sol/medium). `inherit` must be selected explicitly to leave model/effort to the runtime. `max` now means max effort; use `--effort xhigh` for extra high. Resume keeps the prior model/effort and preset when no override is supplied. On resume, an explicit effort preset changes effort while keeping the prior model; `--model` overrides it, and explicit `--preset inherit` clears model/effort overrides. Verify a newer Sol against official documentation and the authenticated runtime catalog before changing all Sol presets together; there is no automatic network model resolver.

Other compatibility changes from v1: existing run directories are no longer reusable; `on-request` and global `doctor --kill-stale` are rejected; JSON final responses and actual artifact checks are enforced; dependencies require setup. Existing v1 result files remain readable using an ordinary JSON reader but cannot be managed as v2 jobs.

Exit codes for synchronous tasks: 0 ok, 2 invalid arguments/dependencies, 4 internal/lost, 5 timeout, 6 execution failed, 8 image missing, 9 verification failed, 130 cancelled. Query commands return 0 when the query succeeds; inspect the returned task status.
