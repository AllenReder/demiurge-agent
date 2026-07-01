---
title: Runner 和 Context
description: 面向贡献者的 turn 执行与 provider context assembly 说明。
---

# Runner 和 Context

Runner 负责 turn lifecycle。Agent Core slots 通过受控接口参与其中；它们不拥有这套
lifecycle。

## Turn Flow

```text
inbound interaction
  -> create or resume session
  -> run bootstrap when needed
  -> run input pipeline
  -> assemble provider context
  -> call provider
  -> execute tool calls through ToolRuntime
  -> continue model/tool loop until final response
  -> run output pipeline
  -> record deliveries and session events
```

## Context Layers

Provider context 可以包含：

- soul text
- skill index and loaded skills
- bootstrap output
- input module placements
- session history
- current user turn
- tool call and tool result history

Context assembler 会决定最终的 provider message 顺序和内容。

## Bootstrap

Bootstrap modules 是 session-start context producers。它们应当在 session 内保持稳定，
并且可以安全地作为参考上下文引用。

## 后台完成 turn

Runner 会为每个 session 保留一个活跃 turn。Background job completion 会被建模为
原始 session 的 synthetic inbound event，而不是直接的 channel output。Channel bridges
会在 user turn 运行时排队 completion events。如果 user input 和 completion 都在等待，
会优先运行 user input，并把待处理的 completion summaries 合并进那个 user turn。

`/stop` 和 foreground cancellation 只影响当前活跃 turn。Background jobs 会继续运行，
直到完成或用户调用 `job(action="cancel")`。

需要用户输入的 background work 会被标记为 `blocked_needs_user`，并且不会自动批准。

## Failure Handling

Slot `failure_policy` 决定失败的 slot 属于 soft 还是 hard。Provider errors、tool
errors、channel delivery errors 和 schedule errors 都由各自的 host-owned 层处理。

## 边界

不要把 provider request construction 或 session ownership 移到 Agent Core 代码里。
