---
title: MCP Runtime
description: Contributor notes for MCP server discovery, naming, transports, and result conversion.
---

# MCP Runtime

The MCP runtime discovers server declarations from Agent Cores and exposes
filtered tools through the host tool registry.

In the current alpha runtime, a catalog cache miss starts or connects to each
enabled server and calls `list_tools()` before the later model-call
`mcp.call:*` capability and approval check. Treat MCP declarations as trusted
code/configuration for now. The frozen target splits `mcp.connect:<server>` from
`mcp.call:<server>` and applies connect policy before spawn, network IO, or
discovery; see [Host Runtime Contracts](runtime-contracts.md#effectruntime).

## Discovery

Declarations live under:

```text
agent/mcp/*.yaml
```

Disabled declarations are ignored. Stdio declarations require `command`.
Streamable HTTP declarations require an `http://` or `https://` URL.

## Naming

Tool names are normalized, server-prefixed, and filtered before they become
visible. The current alpha catalog and dispatcher do not yet share one opaque
resolved entry, so namespacing is not a substitute for connection-bound
dispatch or a single namespace authority.

## Environment and Headers

Declarations can provide environment variables, headers, cwd, timeouts, risk,
approval policy, and parallel-call support. Secrets should come from the host
environment. Environment and header interpolation currently happens while the
catalog is built, before connect approval exists.

## Result Conversion

MCP results are converted into Demiurge tool results before model replay and
display.

## Boundary

The core declares MCP servers. The host owns transport lifecycle, discovery,
timeouts, policy, and tool execution. Some of that ownership is not yet
enforced in the required order in the alpha implementation; do not interpret
the current discovery path as the final EffectRuntime interface.
