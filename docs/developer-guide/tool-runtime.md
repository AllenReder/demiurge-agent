---
title: Tool Runtime
description: Contributor notes for tool discovery, metadata, dispatch, approvals, and results.
---

# Tool Runtime

The tool runtime builds the visible tool registry and executes calls.

## Registry Sources

Tools can come from:

- built-in toolsets
- authored tools under `agent/tools/`
- MCP tools discovered from `agent/mcp/*.yaml`

`agent.yaml` chooses built-in toolsets and can override tool metadata.

## Dispatch

The runtime:

1. resolves the tool registry entry
2. checks enabled state
3. applies capability and approval policy
4. enforces workspace and safety rules where relevant
5. executes the built-in, authored, or MCP tool
6. converts the result for model history and user display

## Authored Tools

Authored tools are adapters. They use the same host-owned dispatch path as
built-ins after discovery.

## MCP Tools

MCP tools are namespaced and filtered to avoid collisions. Transport, discovery,
timeouts, and result conversion are host-owned.

## Boundary

The Agent Core can declare tools. It does not own tool-call replay,
authorization, or provider-specific tool message formatting.
