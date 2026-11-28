# codex-subagent v3.0.0

通用的本地 Codex 子任务调用 Skill，支持 **Antigravity、Claude Code、Codex、WorkBuddy、Cursor、Gemini CLI 以及纯命令行环境**。当你需要显式将任务交给本地 Codex 执行时调用本 Skill。

Runner 默认使用原生 `codex exec --json` CLI 后端，核心完全基于 Python 标准库运行（零强制第三方依赖），支持通过文件或标准输入传入 UTF-8 任务简报，Codex 以自然纯文本回复，并在结束时输出包含客观文件变更与验收结果的结构化 JSON 回执。

## v3.0.0 核心特性

- **CLI 优先 + 标准库零依赖内核**：默认走 `codex exec --json`，直接使用系统 Python 3.10+ 运行。`openai-codex` SDK、`jsonschema` 与 `Pillow` 均改为可选依赖，仅在显式使用 `--backend sdk`、`--schema` 或 `--gen-image` 时按需加载。
- **纯文本回复 + 结构化回执**：Codex 直接回复自然语言，不再注入强制 JSON 模板或 `artifacts` 误判校验，保持 Codex Desktop 会话可读；同时 runner 在 stdout 输出结构化回执（含 `changed_files`、`diff_stat` 与 `--task-spec` 独立检查结果）。
- **默认继承本机 Codex 配置**：移除硬编码默认模型，默认继承 `~/.codex/config.toml` 中的模型与思考强度（effort），仅在显式传 `--model` / `--effort` 时覆盖，并按 effort 自动联动默认超时。
- **动态 CLI 发现与启动前预检**：自动识别最新安装的 Windows Store `OpenAI.Codex` 可执行文件（不再写死版本号），并在创建运行目录前同步检查 CLI、状态目录与 SQLite 可写性。
- **集中日志目录、幂等重试与写锁保护**：新运行默认存放于 `<state-dir>/runs/`（默认 `~/.codex-subagent/runs/`），支持 `--task-id` 幂等去重、Desktop 写锁短退避重试（仍冲突则返回 `thread_busy`），以及跨宿主 10 槽位共享并发队列。
- **精简命令行与文档**：新增 `wait`、`list-threads`、`prune --dry-run`、`--add-dir`、`--state-dir` 与 `--task-file -`（UTF-8 stdin）；退役旧版 Antigravity 专属协调员，主入口精简为一页通用速查 [SKILL.md](SKILL.md)。

## 环境要求与安装

- **Python 3.10+**（核心功能仅依赖标准库）
- **已安装并完成登录鉴权的本地 Codex CLI**（`codex login` 或 Codex Desktop 登录状态）
- **可写的工作区与共享状态目录**（只读核查任务请使用 `--sandbox read-only`）

将 `codex-subagent` 文件夹复制到对应宿主的 Skills 目录即可使用（常规场景无需创建 `.venv`）：

- **Antigravity**：`~/.gemini/config/skills/codex-subagent`（或工作区 `<workspace>/.agents/skills/codex-subagent`）
- **Claude Code**：`~/.claude/skills/codex-subagent`
- **通用 / 项目级目录**：`<workspace>/.agents/skills/codex-subagent`

在 Skill 目录下运行自检：

```text
python scripts/doctor.py
```

如需安装可选依赖（`--sdk`、`--schema`、`--image`）或查看 v2 升级步骤，请参考 [安装与迁移指南](references/host-integration.md)。

此外，[assets/AGENTS.md](assets/AGENTS.md) 提供了一份可选的**主 Agent 专用指令模板**，可复制或合并到宿主的主 Agent 规则文件（如工作区 `AGENTS.md`、Antigravity `GEMINI.md` 或 Claude Code `CLAUDE.md`），切勿直接放在子 Agent 工作区让 Codex 自动加载。

## 常用命令

在 Skill 目录下执行（或直接传入 `scripts/codex_run.py` 的绝对路径）：

```text
python scripts/codex_run.py run --workdir DIR --task "检查当前工作区代码" --sandbox read-only
python scripts/codex_run.py start --workdir DIR --task-file BRIEF.md --task-id ID
python scripts/codex_run.py wait RUN_DIR --timeout 60
python scripts/codex_run.py status RUN_DIR
python scripts/codex_run.py cancel RUN_DIR
python scripts/codex_run.py list-threads --workdir DIR
python scripts/codex_run.py prune --dry-run
```

- 传入 `--task-file -` 可通过 stdin 管道直接传入多行 UTF-8 中文简报。
- `run` 与 `wait` 会在 stdout 输出精简 JSON 回执（`status`、`exit_code`、`thread_id`、`run_id`、`run_dir`、`duration_s`、`final_message`、`changed_files`、`errors`），阶段进度输出到 stderr；`wait` 超时只结束等待，后台任务继续运行。
- 续接已有会话使用 `--resume UUID`，或 `--workdir` 搭配显式 `--lane NAME`（两者均不传则开启新会话）。
- 使用 `--add-dir DIR` 可显式授予额外目录访问权限；使用 `--task-spec FILE` 可在宿主权限下执行独立的 `checks` 与 `required_files` 验收。

## 测试与开发

在 Skill 目录下运行：

```text
python -m unittest discover -s tests
python scripts/doctor.py
```

## 文档索引

- [SKILL.md](SKILL.md) — 一页通用速查（供 Agent 快速加载）
- [CLI 参数说明](references/cli-reference.md) — 子命令、参数、预设与退出码
- [任务简报编写指南](references/delegation-brief.md) — 如何写清自包含的任务上下文
- [任务规范与回执格式](references/events-schema.md) — `--task-spec`、`result.json` 与变更证据说明
- [宿主集成与迁移](references/host-integration.md) — 多宿主部署、可选依赖与 v2 升级
- [图像生成与校验](references/image-pipeline.md) — 可选生图流水线与校验规则
- [故障排查](references/troubleshooting.md) — 常见报错与恢复方式
- [主 Agent AGENTS.md 模板](assets/AGENTS.md) — 可选主 Agent 行为规范模板（仅限宿主主 Agent 使用）

## Contributors

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/Starverse032">
        <img src="https://avatars.githubusercontent.com/u/337628395?v=4&s=128" width="80" height="80" alt="Starverse032" /><br />
        <sub><b>Starverse032</b></sub>
      </a><br />
      <sub>作者与维护者</sub>
    </td>
    <td align="center">
      <a href="https://github.com/apps/gemini-code-assist">
        <img src="https://avatars.githubusercontent.com/in/956858?v=4&s=128" width="80" height="80" alt="Gemini Code Assist" /><br />
        <sub><b>Gemini Code Assist</b></sub>
      </a><br />
      <sub>架构设计、审计与验收</sub>
    </td>
    <td align="center">
      <a href="https://github.com/codex">
        <img src="https://avatars.githubusercontent.com/u/267193182?v=4&s=128" width="80" height="80" alt="Codex" /><br />
        <sub><b>Codex</b></sub>
      </a><br />
      <sub>核心重构与实现</sub>
    </td>
  </tr>
</table>
