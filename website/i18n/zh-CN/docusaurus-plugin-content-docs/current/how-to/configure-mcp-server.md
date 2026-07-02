---
title: 配置 MCP Server
description: 向 Agent Core 添加 MCP server declaration。
---

# 配置 MCP Server

Agent Cores 使用 YAML 文件声明 MCP servers。Host 拥有 transport startup、tool discovery、namespacing、approvals、capability checks 和 tool execution。

默认情况下，loader 会查找：

```text
agent/mcp/*.yaml
```

如果 `agent.yaml` 设置了 `slots.mcp`，该值会覆盖默认 MCP root。

## 添加 Stdio Server

创建 `agent/mcp/docs.yaml`：

```yaml
enabled: true
transport: stdio
command: npx
args:
  - -y
  - "@modelcontextprotocol/server-filesystem"
  - /path/to/project
env:
  API_TOKEN: "${DOCS_TOKEN}"
tools:
  include:
    - search*
  exclude: []
risk: medium
approval_policy: prompt
capability: mcp.call:docs
connect_timeout_seconds: 30
timeout_seconds: 60
supports_parallel_tool_calls: false
```

`transport: stdio` 需要 `command`。`args`、`env` 和 `cwd` 是可选的。相对 `cwd` 值会从 runtime workspace 解析。

构建 MCP catalog 时会解析 `${DOCS_TOKEN}` 这样的环境引用。如果缺少环境变量，host 会记录 diagnostic，并在当前 turn 跳过该 server。

## 添加 Streamable HTTP Server

创建 `agent/mcp/remote_docs.yaml`：

```yaml
enabled: true
transport: streamable_http
url: https://example.test/mcp
headers:
  Authorization: "Bearer ${REMOTE_DOCS_TOKEN}"
tools:
  include: []
  exclude: []
risk: medium
approval_policy: prompt
capability: mcp.call:remote_docs
connect_timeout_seconds: 30
timeout_seconds: 60
supports_parallel_tool_calls: false
```

`transport: streamable_http` 需要 `http://` 或 `https://` URL。

## 授予 MCP Capability

Server manifest 中的 `capability` 命名调用该 server 上 tools 所需的 capability。它本身不会授予该 capability。

把 capability 添加到具体 core manifest 中现有的 `capabilities.defaults` map 下：

```yaml
capabilities:
  defaults:
    mcp.call:docs:
      scope: core
```

如果省略 `capability`，loader 会使用 `mcp.call:<server_id>`。

## 过滤 Tools

使用 `tools.include` 和 `tools.exclude` 限制 tool catalog：

```yaml
tools:
  include:
    - search_docs
    - fetch*
  exclude:
    - fetch_private
```

Filters 会在 host 暴露 MCP server tools 之前匹配其 tool names。暴露出来的 tool names 是 host-safe 且带 namespace 的，例如 `docs__search_docs`。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 中：

```text
/tools
```

如果 server 启动了但 tool discovery 失败，请检查运行时 MCP stderr log：

```text
~/.demiurge/logs/mcp-stderr.log
```

## 边界

Agent Core 声明 MCP servers。Host 拥有 process startup、HTTP transport sessions、environment interpolation、catalog caching、approval prompts、capability enforcement、result conversion 和 runtime cleanup。
