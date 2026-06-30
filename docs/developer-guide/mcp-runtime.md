# MCP Runtime

`McpRuntime` discovers tools from core-local MCP declarations and routes calls
through persistent per-turn server connections.

## Discovery Flow

```text
core.mcp_servers
  -> fingerprint declarations
  -> interpolate env/header values
  -> start or connect server
  -> list tools
  -> filter include/exclude
  -> expose safe names
```

Catalogs are cached by session id, core root, workspace, and declaration
fingerprint.

## Tool Naming

Server and tool names are sanitized. Exposed names use:

```text
<safe_server_name>__<safe_tool_name>
```

The original MCP tool name is used when calling the server.

## Environment Interpolation

Only `env` and `headers` support `${ENV_VAR}` interpolation. Missing
environment variables skip that server and emit `mcp.server_failed`.

## Transports

Current transports:

- `stdio`
- `streamable_http`

Stdio MCP server stderr is appended to:

```text
~/.demiurge/logs/mcp-stderr.log
```

## Result Conversion

Structured MCP content becomes JSON model output. Text blocks become text.
Image/audio/resource blocks are summarized into model-safe text markers or
links.

## Boundary

The first MCP implementation exposes tools only. MCP resources, prompts, OAuth,
dynamic discovery, and CLI management commands are not part of the core-YAML
surface.
