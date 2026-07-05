---
title: self_learning_skills
description: 安装内置 self-learning skills package。
---

# self_learning_skills

`self_learning_skills` 会定期回顾最近的 turns，并让受限的同 core child
agent 更新当前 core 的 skills。它只处理 skills，不读取或写入 memory。

这个 package 会安装：

```text
agent/lib/self_learning_skills/
agent/output/self_learning_skills/
```

output slot 位于 parallel output pipeline。它在 session state 中维护计数器，
达到配置的 interval 后，以如下限制调用同一个 core：

- `input_slots=["base_input"]`
- `output_slots=["base_output"]`
- `tools=["skills_list", "skill_view", "skill_manage"]`
- `use_bootstrap=True`

child 只能使用上面的 skill tools。Skill 写入仍然经过正常的 `skill_manage`
capability 和 approval 检查，变更会在后续 turns 生效。

## 安装

使用交互式 package manager：

```bash
uv run demiurge package
```

或使用 CLI 安装：

```bash
uv run demiurge package install self_learning_skills --core assistant --preview
uv run demiurge package install self_learning_skills --core assistant
```

## 选项

| Option | Default | Meaning |
| --- | --- | --- |
| `interval` | `10` | 每隔多少 turns 运行一次 skill review。 |
| `history_limit` | `40` | 每次 review 提供多少条最近 history messages。 |
| `notify` | `true` | 当 review 更新 skills 或失败时发送 transient notice。 |

## 要求

目标 core 必须暴露 `skills_list`、`skill_view` 和 `skill_manage`，并允许
`skill_manage` 通过 host approval flow 请求 `fs.write`。这个 package 会给
自己的 output slot 授予 `agents.run:*` 和 scoped session-state counter 访问权限。

因为 review 使用 `ctx.agents.run(...)`，当触发 review 时，当前 turn 会等待 child
review 完成。
