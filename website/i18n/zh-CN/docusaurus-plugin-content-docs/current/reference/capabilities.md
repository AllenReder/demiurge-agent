---
title: Capabilities 和 Approvals
description: host-owned capability 和 approval 行为的参考说明。
---

# Capabilities 和 Approvals

Capabilities 描述 effect classes。approval policy 决定一次调用是可以自动运行、必须提示，还是被拒绝。

## 常见 Capabilities

| Capability | Meaning |
| --- | --- |
| `fs.read` | 读取 workspace files。 |
| `fs.write` | 写入 workspace files。 |
| `terminal.exec` | 在 workspace scope 中运行 terminal commands。 |
| `job.control` | 列出、轮询、等待、读取日志或取消 background jobs。 |
| `network.fetch` | 获取 network content。 |
| `schedule.manage` | 管理 core schedule files。 |
| `tool.call:evolve_core` | 通过 host 创建并 promote 一个 candidate core。 |
| `tool.call:rollback_core` | 通过 host 回滚 active core pointer。 |

## Approval Policy

approval policy 顺序：

```text
auto < prompt < deny
```

risk 顺序：

```text
low < medium < high < critical
```

当多个层同时适用时，限制更强的 policy 胜出。

## Tool Metadata

`agent.yaml` 可以覆盖 metadata：

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: prompt
      risk: medium
      capability: network.fetch
```

支持的 metadata keys：

- `risk`
- `capability`
- `approval_policy`
- `model_output_policy`
- `display_policy`
- `enabled`

## Boundary

声明 capability 不等于获得 capability。host 在执行 effects 前会检查 workspace scope、敏感路径、approval policy 和 tool runtime rules。
