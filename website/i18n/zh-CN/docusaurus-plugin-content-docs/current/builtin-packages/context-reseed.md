---
sidebar_position: 4
title: context_reseed
description: 安装并使用内置 bounded continuity-note package。
---

# context_reseed

`context_reseed` 会维护一份有界 continuity note，并在未来 sessions 开始时把它作为 reference context 注入。

当你想要轻量 handoff-style continuity、但不想安装外部 memory provider 时，使用它。

## 安装内容

该 package 会安装：

```text
agent/lib/context_reseed/
agent/bootstrap/context_reseed_bootstrap/
agent/output/context_reseed_output/
agent/skills/context_reseed/
```

它会把一个 bootstrap slot 追加到 serial bootstrap pipeline，并把一个 output slot 追加到 serial output pipeline。

Note 存储在 package-owned component directories 之外：

```text
context/reseed.md
```

Uninstall 会移除 package-owned component directories 和 pipeline entries，但会保留 `context/reseed.md`。

## 安装

使用交互式 manager：

```bash
uv run demiurge package
```

或者用 subcommands 安装：

```bash
uv run demiurge package install context_reseed --core assistant --preview
uv run demiurge package install context_reseed --core assistant
```

## 选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `explicit` | `explicit` 只在用户请求 reseed、handoff、session、context 或 continuity note 时更新。`auto` 在每次 assistant output 后更新。 |
| `max_chars` | `1800` | 从 note 存储并注入的最大字符数。 |
| `notice` | `false` | 当 note 被刷新时，发出 transient output notice。 |

示例：

```bash
uv run demiurge package install context_reseed \
  --core assistant \
  --option mode=auto \
  --option max_chars=2400
```

## 运行时行为

在 session bootstrap 时，bootstrap slot 读取 `context/reseed.md`，sanitize 该 note，将其限制在 `max_chars` 内，把它作为 untrusted data 引用，并作为 background reference context 注入。

Assistant output 后，output slot 会写入新的有界 note。在 `explicit` mode 下，它只会在最新 user input 明确要求 continuity、handoff、session、context 或 reseed notes 时写入。在 `auto` mode 下，它会在每次 assistant output 后写入。

该 package 需要：

| Slot | 能力 |
| --- | --- |
| Bootstrap | `fs.read` |
| Output | `fs.write` |

两个 slots 都使用 `failure_policy: soft`。

## 安全模型

存储的 note 会被视为陈旧、不可信的 reference data。注入前，该 package 会剥离 bidirectional controls，脱敏常见 credential patterns，并阻止常见 prompt-injection phrases。

生成的 note 不是 system 或 developer instructions 的来源。当前 user input 和更高优先级 instructions 仍然优先。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

请求一份 continuity note：

```text
Please write a context reseed note for the next session.
```

然后检查：

```text
~/.demiurge/agents/assistant/context/reseed.md
```

## 卸载

```bash
uv run demiurge package uninstall context_reseed --core assistant --preview
uv run demiurge package uninstall context_reseed --core assistant
```

Uninstall 会移除 package-owned files 和 pipeline entries。它不会移除 `context/reseed.md`。
