---
title: Configure an MCP Server
description: Add an MCP server declaration to an Agent Core.
---

# Configure an MCP Server

Agent Cores declare MCP servers with YAML files. The host owns transport
startup, tool discovery, namespacing, approvals, capability checks, and tool
execution.

On a catalog cache miss, the Host first requires
`mcp.connect:<server_id>` and applies the declaration's risk/approval policy.
Denied authority stops before client construction, process/network startup, and
`list_tools()`. The later tool invocation separately requires the server call
capability and approval. Continue to review a declaration's command, package
runner, URL, environment, and headers before enabling it: sanitized secret
binding now limits stdio children to an allowlisted environment plus the
declaration's approved `env` entries, while full URL safety remains a later
security layer.

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

Environment references such as `${DOCS_TOKEN}` are resolved only after connect
authority allows the server. If a variable is missing, the Host records a
diagnostic and skips that server for the turn. A configured cwd must resolve
inside the Host workspace before approval or client construction.

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

The Host uses a separate `mcp.connect:<server_id>` capability for
spawn/connect/discovery. The manifest's `capability` remains the **call**
capability for tools from that server.

Add the capability under the existing `capabilities.defaults` map in the
concrete core manifest:

```yaml
capabilities:
  defaults:
    mcp.connect:docs:
      scope: core
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

`list_tools()` uses `connect_timeout_seconds`. Discovery uses one runtime-wide
limit of four servers across sessions. A failed server does not block or close a
healthy peer; its diagnostic is cached for 30 seconds before only that server
is retried. Connect denial is rechecked per server on the next turn.
Declaration or authority changes close the older session-bound catalog and
require connect reapproval. Removing every declaration closes remaining
connections, and starting or resuming another session tracks cleanup of the
previous session. Delegated child sessions use their own Host-issued authority
and release MCP connections when the child run ends. Evolution review records a
secret-safe MCP security diff and prints a content-bound `mcp-review:<sha256>`
token. Promotion requires that exact token; missing or stale tokens leave the
live and previous Git refs unchanged.

If a server starts but tool discovery fails, inspect the runtime MCP stderr log:

```text
~/.demiurge/logs/mcp-stderr.log
```

## Boundary

An Agent Core declares MCP servers. The host owns process startup, HTTP
transport sessions, environment interpolation, catalog caching, approval
prompts, capability enforcement, result conversion, and runtime cleanup. MCP is
still not a sandbox: stdio commands and remote URLs remain trusted effects, and
stdio processes receive an allowlisted environment plus only declaration-bound
values after approval. Shared URL validation remains later security work.
