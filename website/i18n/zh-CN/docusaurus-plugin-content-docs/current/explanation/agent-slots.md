---
title: Agent Slots
description: 理解 Agent Slots 如何作为 Agent Core 的可演化交互边界。
---

# Agent Slots

**Agent Slot** 是 Agent Core 的可演化交互边界：它让 Core 定义的行为逻辑在受
治理的位置介入 agent loop，并组合 tools、skills、MCP、state 或其他 agents，而不
需要修改 Host harness。

Slot 最大的特点不是它提供了哪种能力，而是它定义能力可以在哪里影响 agent loop。
Host 仍然拥有 scheduling、provider calls、tool dispatch、approvals、delivery、
state enforcement、promotion 和 rollback。

Slot 和它组合的对象是不同概念：

- **tool** 是 model-callable action。
- **skill** 是可复用知识、workflow 或 policy。
- **MCP** 是外部 tools 和 context 的协议。
- **agent** 是另一个可运行的 loop。
- **package** 是 distribution unit，可以一起安装 slots、tools、skills、libraries
  和 child cores。

当前 Demiurge slots 是 bootstrap、input 和 output slots。它们让 Agent Core 添加
session context、塑造 current-turn input，并处理 final output。未来新的 slot kind
应该代表新的受治理交互边界，而不是普通 feature category。

这让 slots 成为自然的 evolution surface。Candidate Agent Core 可以用文件形式替换、
重排或组合 slot behavior，而 host 仍把高风险效果留在稳定 contracts 后面。
