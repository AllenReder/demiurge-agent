---
title: Configure an MCP Server
description: Add a core-local MCP server declaration.
---

# Configure an MCP Server

Agent Cores can declare MCP servers under `agent/mcp/`. The host owns
transport, discovery, capability checks, approvals, and tool calls.

## Add a Stdio Server

Create:

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

Stdio servers require `command`.

## Add a Streamable HTTP Server

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

Streamable HTTP servers require an `http://` or `https://` URL.

## Filter Tools

```yaml
tools:
  include:
    - search
    - fetch
  exclude: []
```

Tool names are namespaced by the host to avoid collisions with built-in and
authored tools.

## Verify

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Inside the TUI:

```text
/tools
```

MCP stderr logs are written under the runtime home logs area.

## Boundary

The core declares MCP servers. It does not own the transport process, network
permissions, approval policy, or tool execution loop.
