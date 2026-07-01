---
title: 配置 MCP 服务器
description: 添加一个 core-local 的 MCP server declaration。
---

# 配置 MCP 服务器

Agent Cores 可以在 `agent/mcp/` 下声明 MCP servers。host 拥有 transport、
discovery、capability checks、approvals 和 tool calls。

## 添加 Stdio Server

创建：

```text
agent/mcp/filesystem.yaml
```

```yaml
enabled: true
transport: stdio
command: npx
args:
  - -y
  - "@modelcontextprotocol/server-filesystem"
  - /path/to/project
env: {}
risk: medium
approval_policy: prompt
supports_parallel_tool_calls: false
tools:
  include: []
  exclude: []
```

Stdio servers 需要 `command`。

## 添加 Streamable HTTP Server

```yaml
enabled: true
transport: streamable_http
url: https://example.com/mcp
headers:
  Authorization: "Bearer ${MCP_TOKEN}"
risk: medium
approval_policy: prompt
supports_parallel_tool_calls: false
tools:
  include: []
  exclude: []
```

Streamable HTTP servers 需要 `http://` 或 `https://` URL。

## 过滤 Tools

```yaml
tools:
  include:
    - search
    - fetch
  exclude: []
```

Tool names 由 host 命名空间化，以避免和 built-in 以及 authored tools 冲突。

## 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 内：

```text
/tools
```

MCP stderr logs 会写到 runtime home 的 logs 区域下。

## 边界

core 声明 MCP servers。它不拥有 transport process、network permissions、
approval policy 或 tool execution loop。
