---
title: MCP 运行时
description: 面向贡献者的 MCP server 发现、命名、transport 和结果转换说明。
---

# MCP 运行时

MCP runtime 会从 Agent Cores 中发现 server declarations，并通过 host tool
registry 暴露经过过滤的 tools。

## 发现

Declarations 位于：

```text
agent/mcp/*.yaml
```

已禁用的 declarations 会被忽略。Stdio declarations 需要 `command`。Streamable HTTP
declarations 需要 `http://` 或 `https://` URL。

## 命名

Tool 名称会被规范化并加上命名空间，以避免与 built-in 和 authored tools 冲突。
Include/exclude filters 会在 tools 对外可见之前应用。

## 环境和 Headers

Declarations 可以提供 environment variables、headers、cwd、timeouts、risk、approval
policy 和 parallel-call support。Secrets 应该来自 host environment。

## 结果转换

MCP results 会在模型 replay 和显示之前转换为 Demiurge tool results。

## 边界

Core 负责声明 MCP servers。Host 负责 transport lifecycle、discovery、timeouts、
approval policy 和 tool execution。
