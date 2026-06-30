# Tool Runtime

`ToolRuntime` builds the visible tool registry and executes tool calls through
host-owned checks.

## Registry Sources

Visible tools come from:

1. enabled built-in toolsets in the core manifest;
2. authored tools under `agent/tools/`;
3. MCP tools discovered from `agent/mcp/*.yaml`.

The registry entry records name, description, schema, source, risk, capability,
approval policy, model output policy, display policy, and slot path if
applicable.

## Dispatch Flow

```text
ToolCall
  -> visible tool check
  -> built-in / authored / MCP dispatch
  -> capability check
  -> approval and workspace guard when needed
  -> ToolResult
  -> session message + event
```

## Built-In Tools

Built-in schemas and baseline metadata live in `demiurge/tools/registry.py`.
Execution lives in `demiurge/tools/runtime.py`.

Core metadata can make built-in tools stricter but cannot lower their baseline
risk or approval policy.

## Authored Tools

Authored tools are loaded from slot definitions. Their `slot.yaml` controls
schema, risk, approval policy, capabilities, output shaping, and enabled state.

Authored tools receive host-injected context and delivery clients. They should
return `ToolResult`.

## MCP Tools

MCP tool calls are routed through `McpRuntime`. Each tool defaults to capability
`mcp.call:<server_id>` unless the server declaration sets another capability.

## Failure Modes

- Unknown tool: returns a model-visible error.
- Tool not enabled for core: returns an allowlist error.
- Workspace escape or denied capability: returns a host error without executing
  the requested effect.
- Terminal hardline blocks cannot be bypassed by approval config.
