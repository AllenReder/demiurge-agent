---
title: Delivery 和 History
description: 输出 delivery timing 和 history policy 的参考说明。
---

# Delivery 和 History

Output modules 可以通过 host-owned delivery interfaces 交付文本、artifacts、媒体和结构化结果。

## History Policy

常见 policy：

| Policy | Meaning |
| --- | --- |
| `persist` | 把 delivered content 存入持久 session history。 |
| `transient` | 以 live output 的形式交付，不写入持久 assistant history。 |

应该为需要在后续 context 中可见的 assistant answers 使用 persisted delivery。`progress`、notices 和只用于 live 的 status 应使用 transient delivery。

## Timing

input 和 output modules 可以在 model output 被 persisted 之前或之后发出 live deliveries。不要假设 live display order 与 persisted history order 完全一致。

## Output Module 示例

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

## Boundary

authored modules 请求 delivery。host 拥有 session records、channel delivery、route context、artifact records 和 persistence。
