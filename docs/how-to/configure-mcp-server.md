---
title: Configure an MCP Server
description: Add an MCP server declaration to an Agent Core.
---

# Configure an MCP Server

Agent Cores declare MCP servers with YAML files. The host owns transport
startup, tool discovery, namespacing, approvals, capability checks, and tool
execution.

Current alpha security boundary: on a catalog cache miss, the Host may
spawn/connect and call `list_tools()` before the later `mcp.call:*` capability
and approval check. Review a declaration's command, package runner, URL, cwd,
environment, and headers as trusted code/configuration before enabling it. The
target runtime adds a separate `mcp.connect:<server_id>` effect before any
connect or discovery side effect.

By default, the loader looks under:

```text
agent/mcp/*.yaml
```

If `agent.yaml` sets `slots.mcp`, that value overrides the default MCP root.

## Add a Stdio Server

Create `agent/mcp/docs.yaml`:

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

`transport: stdio` requires `command`. `args`, `env`, and `cwd` are optional.
Relative `cwd` values are resolved from the runtime workspace.

Environment references such as `${DOCS_TOKEN}` are resolved when the MCP catalog
is built. If an environment variable is missing, the host records a diagnostic
and skips that server for the turn.

## Add a Streamable HTTP Server

Create `agent/mcp/remote_docs.yaml`:

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

`transport: streamable_http` requires an `http://` or `https://` URL.

## Grant the MCP Capability

The server manifest's `capability` names the capability required to call tools
from that server. It does not grant the capability by itself.

This is currently a **call** capability. It does not yet authorize or deny the
earlier spawn/connect/discovery step.

Add the capability under the existing `capabilities.defaults` map in the
concrete core manifest:

```yaml
capabilities:
  defaults:
    mcp.call:docs:
      scope: core
```

If `capability` is omitted, the loader uses `mcp.call:<server_id>`.

## Filter Tools

Use `tools.include` and `tools.exclude` to limit the tool catalog:

```yaml
tools:
  include:
    - search_docs
    - fetch*
  exclude:
    - fetch_private
```

Filters match MCP server tool names before the host exposes them. Exposed tool
names are host-safe and namespaced, for example `docs__search_docs`.

## Verify

Run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Inside the TUI:

```text
/tools
```

If a server starts but tool discovery fails, inspect the runtime MCP stderr log:

```text
~/.demiurge/logs/mcp-stderr.log
```

## Boundary

An Agent Core declares MCP servers. The host owns process startup, HTTP
transport sessions, environment interpolation, catalog caching, approval
prompts, capability enforcement, result conversion, and runtime cleanup. The
ownership statement describes the intended Host policy owner; the alpha
connect/discovery ordering limitation above remains until `EffectRuntime` is
implemented.
