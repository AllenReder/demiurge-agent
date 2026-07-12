---
title: 配置 MCP Server
description: 为 Agent Core 添加 MCP server declaration。
---

# 配置 MCP Server

Agent Core 使用 YAML 文件声明 MCP server。Host 拥有 transport startup、tool discovery、
namespacing、approvals、capability checks 与 tool execution。

Catalog cache miss 时，Host 会先要求 `mcp.connect:<server_id>`，并应用 declaration 的
risk/approval policy。Authority 缺失或被拒绝时，会在 client construction、process/network
startup 与 `list_tools()` 前停止。后续 tool invocation 还会独立要求 server call capability
和 approval。启用前仍应审查 declaration 的 command、package runner、URL、environment 与
headers；sanitized secret binding 与完整 URL safety 属于后续 security layer。

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

`${DOCS_TOKEN}` 之类的 environment reference 只会在 connect authority 允许 server 后
解析。如果缺少 environment variable，Host 会记录 diagnostic，并在该 turn 跳过对应
server。Configured cwd 必须在 approval 或 client construction 前解析到 Host workspace 内。

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

Host 使用独立的 `mcp.connect:<server_id>` capability 管理 spawn/connect/discovery；manifest
中的 `capability` 仍是该 server tools 的 **call** capability。

在具体 core manifest 现有的 `capabilities.defaults` map 下添加 capability：

```yaml
capabilities:
  defaults:
    mcp.connect:docs:
      scope: core
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

`list_tools()` 使用 `connect_timeout_seconds`。Discovery 在整个 runtime 内跨 session 最多
并发四个 server；失败 server 不会阻塞或关闭健康 peer，其 diagnostic 会缓存 30 秒后只重试
该 server。Connect denial 会在下一个 turn 按 server 重新检查。Declaration 或 authority
变化会关闭旧的 session-bound catalog，并要求 connect reapproval。删除全部 declaration 会
关闭剩余 connection；切换到新 session 或 resume 其他 session 时会跟踪清理旧 session。
Delegated child session 使用自己的 Host-issued authority，并在 child run 结束时释放 MCP
connection。Evolution review 会记录 secret-safe MCP security diff，并输出内容绑定的
`mcp-review:<sha256>` token；promotion 必须原样返回该 token。token 缺失或已过期时 live
与 previous Git refs 保持不变。

如果 server 已启动但 tool discovery 失败，检查 runtime MCP stderr log：

```text
~/.demiurge/logs/mcp-stderr.log
```

## 边界

Agent Core 声明 MCP servers。Host 拥有 process startup、HTTP transport sessions、
environment interpolation、catalog caching、approval prompts、capability enforcement、
result conversion 与 runtime cleanup。MCP 仍不是 sandbox：stdio command 与 remote URL
仍是 trusted effect；后续 security 工作会补 sanitized secret binding 与共享 URL
validation。
