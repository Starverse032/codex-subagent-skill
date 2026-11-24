# Antigravity Desktop and other hosts

The Antigravity desktop app loads global skills from `~/.gemini/config/skills/<skill-folder>/` and workspace skills from `<workspace>/.agents/skills/<skill-folder>/`. Install this folder in one of those locations. Start a new local conversation. The user can type `/codex-subagent` or explicitly ask to call a Codex subagent in natural language. A skill being discoverable alone does not authorize dispatch.

## Setup and identity

- Read the originating project's title and canonical workspace from the current local conversation. For a conversation without a project, derive a concise title and use a dedicated, authorized workspace. Codex can work on frontend, design, backend, and other tasks through its available tools.
- Use a stable host conversation ID if the host exposes it. Otherwise generate a random conversation token once, retain it in the chat and its project record, and reuse it verbatim. Never infer identity from the project title alone or reuse a token from a different chat.
- Run the skill's private Python by absolute path when the host does not have Python on PATH. If the host asks permission to run the command, use its normal permission flow.

## Gemini coordinator subagents

The main Gemini owns decomposition, dependencies and final acceptance. Invoke the bundled [codex-lane-coordinator](../assets/antigravity/codex-lane-coordinator.md) with Antigravity's `invoke_subagent` tool. Install the definition to `~/.gemini/config/agents/codex-lane-coordinator.md` or `<workspace>/.agents/agents/codex-lane-coordinator.md`. If the named agent is unavailable and the host provides `self` with the needed file and command tools, invoke `self` with the coordinator definition in its prompt. If the host cannot provide either coordinator, explain the missing capability and confirm with the user before switching to direct dispatch.

Use the inherited workspace and give each coordinator the originating chat ID, project title, absolute working directory, absolute skill and Python paths, lane name, assigned scope and files, constraints, dependencies, desired effort and acceptance criteria. Pass the main chat ID verbatim to all coordinators, since a Gemini subagent has its own conversation ID. A simple task still uses one coordinator and one Codex lane. Keep dependent work serialized. The main agent does not dispatch duplicate jobs for lanes it has assigned.

Each coordinator writes an individual task file, enqueues Codex, polls its run to completion, inspects the diff or deliverable, and runs independently authorized checks. It returns the run path, Codex project and thread IDs, checks and results, and any blockers. It asks Codex in the same lane to fix rejected work. The main agent assigns cross-lane integration to Codex through a coordinator, then reviews the combined evidence. Other clients can apply this same hierarchy with their own native subagents and tools.

## Codex Desktop project

The runner creates or recovers exactly one **local Codex App Server project** for the originating chat. Display name: `Antigravity | <project title>` or `<Host> | <project title>` for other clients. The pipe is a display character. The project's display name and the files used by the run are configured separately.

1. Run `start` with the originating identity. The SDK negotiates Codex's experimental App Server project API and creates the project with a conversation-derived idempotency key. The project has no attached source roots. Each turn still uses the explicitly supplied workdir and sandbox. All lanes share the same project. The runner verifies the returned thread's project ID before sending the task. An unsupported runtime reports an error before inference. Use a recent native CLI that exposes `project/create` and `thread/start.projectId` in `codex app-server generate-ts --experimental`.
2. After completion, inspect `result.json` for `codex_project_id` and `thread_id`. The `project` command lists the shared project, previous runs and reusable threads. A named project in the runner or App Server is evidence of API grouping. Check the Desktop sidebar separately before claiming the project is visible there.
3. Desktop builds can show a separate set of projects. On the tested build, projects created by App Server did not immediately appear in the app's project list, and the app's move tool rejected SDK threads outside its sidebar. Where supported by the build, the local chat deep link `codex://threads/<thread-id>` opens a saved thread (see the Windows Desktop notes below for builds where second-instance activation fails). Verify the native UI result before reporting sidebar membership. If the build requires a UI project, use **File → New Project…** and the exact title. Attach a source folder only with appropriate permission. Keep the App Server ID and Desktop UI ID distinct unless the API confirms they are the same. Avoid duplicate work runs to obtain a sidebar entry.
4. `project ... --codex-project-id ID` binds an **App Server** project confirmed by `project/read`. The runner validates the title and refuses a conflicting binding. Do not enter a legacy Desktop sidebar ID unless the App Server can read it.

### Windows Codex Desktop sidebar sync notes

Verified against Windows Store build `26.1111.2228.0` (`codex-cli 0.169.0-alpha.11.1`, Chromium Owl `157.0.8103.78`):

- **Project matching and refresh without restarting Desktop:** The Desktop sidebar reads local thread lists from `codex.db` (`local_thread_catalog`) rather than continuously polling `state_5.sqlite`, and groups threads into a local UI project by matching each thread's `cwd` against that project's `rootPaths`. Once the workspace folder is bound to a Desktop project (by opening or creating the project in Desktop UI once, or binding its `rootPaths` offline while Desktop is closed), collapse and re-expand that project in the Desktop sidebar after external runs finish. Collapsing and re-expanding triggers `repairProject` (`repairLocalProject`) and pulls newly created SDK/CLI threads from `state_5.sqlite` into the sidebar without restarting Desktop.
- **Second-instance handoff limitation on build `26.1111.2228.0`:** While Codex Desktop is already running, invoking `codex://...` deep links, `IApplicationActivationManager` (`OpenAI.Codex_2p2nqsd0c76g0!App --open-project ...`), or `chatgpt-desktop.exe --user-data-dir=... --open-project ...` crashes the secondary process into Crashpad (`web\Codex\Crashpad\reports`) without reaching the running main instance. Invoking `chatgpt-desktop.exe` without `--user-data-dir` also targets a separate `web\chatgpt-desktop` profile directory. Do not rely on hot deep-link or CLI handoff to create projects or wake threads on this build.

```powershell
& '<SKILL_DIR>/.venv/Scripts/python.exe' '<SKILL_DIR>/scripts/codex_run.py' project --host Antigravity --conversation '<stable-id>' --project-title 'Inventory' --workdir 'C:/repo'
& '<SKILL_DIR>/.venv/Scripts/python.exe' '<SKILL_DIR>/scripts/codex_run.py' start --host Antigravity --conversation '<stable-id>' --project-title 'Inventory' --workdir 'C:/repo' --task-file 'C:/repo/task.md' --effort high
```

On follow-ups use the same host, conversation, title, workspace, and lane. The queue selects the latest completed thread in that lane at admission. The default lane `main` serializes dependent work. Give independent scopes distinct lanes only when they can run concurrently without touching the same files. Use `--fresh-session` for unrelated work or irrecoverable context; the new thread still belongs in the same Desktop project. After `failed` or `lost`, inspect changes before retrying. A running Codex task retains ownership until it finishes or is cancelled.

Sources: [Antigravity skills](https://antigravity.google/docs/skills), [Gemini subagents](https://antigravity.google/docs/subagents), [Codex projects](https://learn.chatgpt.com/docs/projects).
