# MCP Servers

MCP servers are declared by a concrete agent core under `agent/mcp/*.yaml`.
The core owns the declarations; the host owns transports, tool discovery,
capabilities, approvals, logging, and tool execution.

## Minimal Stdio Server

`agent/mcp/docs.yaml`:

```yaml
enabled: true
transport: stdio
command: npx
args:
  - -y
  - "@modelcontextprotocol/server-filesystem"
  - .
env: {}
cwd: null

tools:
  include: []
  exclude: []

risk: medium
approval_policy: prompt
capability: null
connect_timeout_seconds: 30
timeout_seconds: 60
supports_parallel_tool_calls: false
```

Declare the matching capability in the concrete core:

```yaml
capabilities:
  defaults:
    mcp.call:docs: {}
```

## Streamable HTTP Server

```yaml
enabled: true
transport: streamable_http
url: "https://example.com/mcp"
headers:
  Authorization: "Bearer ${MCP_EXAMPLE_TOKEN}"
```

Only `env` and `headers` support `${ENV_VAR}` interpolation. If a variable is
missing, that server is skipped for the turn and the host emits an MCP
diagnostic event.

## Tool Names

MCP tools are exposed as:

```text
<safe_server_name>__<safe_tool_name>
```

The host calls the original tool name on the original server.

## Success Check

```bash
uv run demiurge --provider fake
```

Use `/tools`. MCP tools should appear after discovery if the server starts and
its env requirements are satisfied.

## Current Limits

The current MCP surface exposes server tools. MCP resources, prompts, OAuth,
dynamic discovery commands, and CLI management commands are not part of the
first core-YAML surface.
