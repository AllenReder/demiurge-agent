---
title: 配置 MCP Server
description: 为 Agent Core 添加 MCP server declaration。
---

# 配置 MCP Server

Agent Core 使用 YAML 文件声明 MCP server。Host 拥有 transport startup、tool discovery、
namespacing、approvals、capability checks 与 tool execution。

当前 alpha 安全边界：catalog cache miss 时，Host 可能在之后的 `mcp.call:*` capability
与 approval check 之前 spawn/connect 并调用 `list_tools()`。启用 declaration 前，应把
其中的 command、package runner、URL、cwd、environment 与 headers 当作可信代码/配置
审查。目标运行时会增加独立的 `mcp.connect:<server_id>` effect，并在任何 connect 或
discovery side effect 前执行。

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

`transport: stdio` 必须包含 `command`。`args`、`env` 与 `cwd` 可选。相对 `cwd` 会从
runtime workspace 解析。

`${DOCS_TOKEN}` 之类的 environment reference 会在构建 MCP catalog 时解析。如果缺少
environment variable，Host 会记录 diagnostic，并在该 turn 跳过对应 server。

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

`transport: streamable_http` 必须使用 `http://` 或 `https://` URL。

## 授予 MCP Capability

Server manifest 的 `capability` 指定调用该 server tools 所需的 capability。它本身不会
授予 capability。

目前这是一个 **call** capability，尚不能授权或拒绝更早的 spawn/connect/discovery step。

在具体 core manifest 现有的 `capabilities.defaults` map 下添加 capability：

```yaml
capabilities:
  defaults:
    mcp.call:docs:
      scope: core
```

如果省略 `capability`，loader 会使用 `mcp.call:<server_id>`。

## 过滤 Tools

使用 `tools.include` 与 `tools.exclude` 限制 tool catalog：

```yaml
tools:
  include:
    - search_docs
    - fetch*
  exclude:
    - fetch_private
```

Filter 会在 Host 暴露 tool 前匹配 MCP server tool name。暴露的 tool name 是 Host-safe
且 namespaced 的，例如 `docs__search_docs`。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 中运行：

```text
/tools
```

如果 server 已启动但 tool discovery 失败，检查 runtime MCP stderr log：

```text
~/.demiurge/logs/mcp-stderr.log
```

## 边界

Agent Core 声明 MCP servers。Host 拥有 process startup、HTTP transport sessions、
environment interpolation、catalog caching、approval prompts、capability enforcement、
result conversion 与 runtime cleanup。该 ownership statement 描述目标 Host policy
owner；在实现 `EffectRuntime` 前，上述 alpha connect/discovery ordering limitation 仍然
存在。
