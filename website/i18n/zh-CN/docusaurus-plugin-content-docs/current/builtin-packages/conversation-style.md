---
sidebar_position: 5
title: conversation_style
description: 安装可配置的 per-turn conversation style hints 和匹配的 style skill。
---

# conversation_style

`conversation_style` 会在每次 model request 前添加可配置的 communication style hints。它也可以激活一个 packaged skill，用来强化相同的 style preference。

当 core 应该持续偏好 concise、balanced、detailed 或 technical responses，但你不想重写 core main prompt 时，使用它。

## 安装内容

该 package 会安装：

```text
agent/input/conversation_style/
agent/skills/conversation_style/
```

它会把 input slot 追加到 serial input pipeline。

## 安装

使用交互式 manager：

```bash
uv run demiurge package
```

或者用 subcommands 安装：

```bash
uv run demiurge package install conversation_style --core assistant --preview
uv run demiurge package install conversation_style --core assistant
```

安装 technical style：

```bash
uv run demiurge package install conversation_style \
  --core assistant \
  --option style=technical
```

## 选项

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `style` | `balanced` | Reply style。可选值：`concise`、`balanced`、`detailed`、`technical`。 |
| `channel_hint` | `true` | 当 channel metadata 存在时，添加轻量 Telegram 或 TUI formatting hints。 |
| `activate_skill` | `true` | 为每个 turn 激活 packaged `conversation_style` skill。 |

Style 模式：

| 模式 | 行为 |
| --- | --- |
| `concise` | 简短、易扫读的回答，只包含必要 context。 |
| `balanced` | 直接结果，加上相关 reasoning、caveats 和 next steps。 |
| `detailed` | 更多 explanation、trade-offs 和可复现 details。 |
| `technical` | 精确技术语言、明确 references、assumptions 和 verification。 |

## 运行时行为

Input slot 会在每次 model request 前注入一条低优先级 system context hint。该 hint 的优先级低于 system instructions、developer instructions 和最新 user request。

当 `channel_hint=true` 时，该 slot 会为 Telegram 或 TUI 等已知 channels 添加一条简短的 channel-specific formatting hint。

当 `activate_skill=true` 时，该 slot 需要：

```text
skill.activate:conversation_style
```

被激活的 skill 会告诉 model，把 style 作为 preference，而不是 policy override。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

运行一个 turn，并检查 response style。如果最新 user request 要求不同的详细程度，最新 user request 应该优先。

## 卸载

```bash
uv run demiurge package uninstall conversation_style --core assistant --preview
uv run demiurge package uninstall conversation_style --core assistant
```

Uninstall 会移除 package-owned input slot、skill 和 pipeline entry。
