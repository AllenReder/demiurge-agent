---
title: CLI 参考
description: Demiurge CLI 的命令和选项参考。
---

# CLI 参考

除非你使用的是已安装的 managed binary，否则所有 Python 命令都应通过源代码 checkout 中的 `uv` 来运行。

## 主 TUI 命令

```bash
uv run demiurge [options]
```

常见选项：

| Option | Meaning |
| --- | --- |
| `--home HOME` | runtime home directory。 |
| `--core CORE` | 要运行的 core id。 |
| `--agents-root AGENTS_ROOT` | 源 agents root 覆盖值。 |
| `--provider PROVIDER` | provider profile id、`auto` 或 `fake`。 |
| `--model MODEL` | model 覆盖值。 |
| `--fake-script FAKE_SCRIPT` | fake provider script JSON。 |
| `--workspace WORKSPACE` | 文件和 terminal tools 的 workspace root。 |
| `--timezone TIMEZONE` | runtime IANA timezone 覆盖值。 |
| `--session SESSION` | 要创建或恢复的 session id。 |
| `--resume RESUME` | 要恢复的现有 session id。 |
| `--tool-display quiet|summary|full` | TUI tool call display level。 |

## `init`

```bash
uv run demiurge init
uv run demiurge init --check
uv run demiurge init --json
uv run demiurge init --refresh assistant
uv run demiurge init --refresh all
```

初始化或刷新 runtime home 下的 runtime templates。`--check` 是只读的。

## `doctor`

```bash
uv run demiurge doctor
uv run demiurge doctor --core assistant
uv run demiurge doctor --json
```

检查 runtime/source template drift。

## `core`

```bash
uv run demiurge core status
uv run demiurge core versions
uv run demiurge core check
uv run demiurge core save
uv run demiurge core diff
uv run demiurge core discard --yes
uv run demiurge core evolve start Improve concise replies
uv run demiurge core evolve review <run_id>
uv run demiurge core evolve promote <run_id>
uv run demiurge core evolve discard <run_id>
uv run demiurge core rollback
uv run demiurge core rollback <revision>
```

检查并修改 Git-backed runtime agents tree。Revisions 是
`~/.demiurge/.core.git` 中的 commits。

`core check` 会对 live agents tree 运行 host-owned gates。`core evolve start`
会在 `.evolve/runs/<run_id>/agents` 下创建隔离 worktree。Review 会记录
`refs/demiurge/runs/<run_id>`，promote 会推进 `refs/demiurge/live`，rollback
会创建新的 rollback commit。

`core diff` 会显示 `~/.demiurge/agents` 中的 local agent edits，不写入文件。
`core save` 会验证这些 edits，并提交为新的 `core_revision`。`core discard --yes`
会把 live checkout 重置到 `refs/demiurge/live`，并移除未跟踪的 local agent edits。

run/edit workflows 会在加载 live core 前自动保存 local agent edits。只读命令不会创建
commit。`core evolve promote` 和 `core rollback` 这类 revision switch workflow 如果仍
发现未保存的 local agent edits，会拒绝继续；先 save 或 discard。

## `setup`

```bash
uv run demiurge setup status
uv run demiurge setup providers list
uv run demiurge setup providers add openai --preset openai --set-default
uv run demiurge setup providers edit openai --base-url https://api.openai.com/v1
uv run demiurge setup providers add anthropic --api-mode anthropic-messages --base-url https://api.anthropic.com/v1
uv run demiurge setup providers remove openai
uv run demiurge setup providers set-default openai
uv run demiurge setup providers test openai --model <model-name>
uv run demiurge setup model set --core assistant --provider openai --model <model-name>
uv run demiurge setup timezone set Asia/Shanghai
uv run demiurge setup timezone clear
```

当前 provider presets 包括：

```text
anthropic, dashscope, deepseek, minimax, minimax-cn, moonshot, openai,
openrouter, siliconflow, zai
```

## `package`

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
uv run demiurge package install <package_id|repo/package_id> --core assistant
uv run demiurge package install <package_id|repo/package_id> --core assistant --preview
uv run demiurge package install <package_id|repo/package_id> --core assistant --option key=value
uv run demiurge package uninstall <package_id|repo/package_id> --core assistant
uv run demiurge package uninstall <package_id|repo/package_id> --core assistant --force-drift
uv run demiurge package repo list
uv run demiurge package repo add ./local-packages --alias local --trust
uv run demiurge package repo add https://github.com/user/demiurge-packages.git --alias community --ref main --trust
uv run demiurge package repo sync community
uv run demiurge package repo remove community
```

外部 path 和 git repositories 在可以安装本地 Agent Slot code 之前，必须先被信任。

## `update`

```bash
demiurge update
demiurge update --ref v0.4.0
demiurge update --skip-init-check
```

更新 managed checkout，并可选地运行一次只读的 runtime drift check。

## `gateway`

```bash
uv run demiurge gateway --core assistant
uv run demiurge gateway --core assistant --provider fake
uv run demiurge gateway --core assistant --timezone Asia/Shanghai
```

运行所选 core 的已启用 external channels。

## Verification Commands

在文档或 CLI surface 变更后使用这些命令：

```bash
uv run demiurge --help
uv run demiurge init --help
uv run demiurge core --help
uv run demiurge setup --help
uv run demiurge package --help
uv run demiurge gateway --help
```
