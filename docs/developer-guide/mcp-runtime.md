---
title: MCP Runtime
description: Contributor notes for MCP server discovery, naming, transports, and result conversion.
---

# MCP Runtime

The MCP runtime discovers server declarations from Agent Cores and exposes
filtered tools through the host tool registry.

## Discovery

Declarations live under:

```text
agent/mcp/*.yaml
```

Disabled declarations are ignored. Stdio declarations require `command`.
Streamable HTTP declarations require an `http://` or `https://` URL.

## Naming

Tool names are normalized and namespaced to avoid collisions with built-in and
authored tools. Include/exclude filters are applied before tools become visible.

## Environment and Headers

Declarations can provide environment variables, headers, cwd, timeouts, risk,
approval policy, and parallel-call support. Secrets should come from the host
environment.

## Result Conversion

MCP results are converted into Demiurge tool results before model replay and
display.

## Boundary

The core declares MCP servers. The host owns transport lifecycle, discovery,
timeouts, approval policy, and tool execution.
