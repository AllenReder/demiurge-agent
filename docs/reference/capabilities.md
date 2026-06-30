# Capabilities and Approvals

Capabilities describe what a slot or tool is allowed to request. Approval
policy decides whether the host should allow, prompt, or deny a risky action.

## Built-In Capability Examples

| Capability | Typical use |
| --- | --- |
| `fs.read` | Read workspace files. |
| `fs.write` | Write or patch workspace files. |
| `terminal.exec` | Run shell commands or manage processes. |
| `network.fetch` | Fetch URLs. |
| `state.read` | Read host-managed state. |
| `state.write` | Submit typed state proposals. |
| `tool.call:<name>` | Call a host-visible tool from authored code. |
| `agents.run:<core>` | Run a child core and await the result. |
| `agents.spawn:<core>` | Spawn a child core. |
| `mcp.call:<server_id>` | Call tools from a declared MCP server. |

## Approval Order

Policy values:

- `auto`
- `prompt`
- `deny`

Agent cores can make built-in tool policy stricter. They cannot weaken the host
security baseline for built-in tools.

## Risk Order

Risk values:

- `low`
- `medium`
- `high`
- `critical`

Core metadata can raise built-in tool risk. Authored and MCP tools can declare
their own risk and approval policy.

## Terminal Guard

Terminal commands pass through a host command guard. Safe inspect/test/build
commands may run automatically. Promptable dangerous commands require approval.
Hardline catastrophic commands are blocked before approval.

## Success Check

Use `/tools` to inspect visible tools and `/events` to inspect approval events.

## Boundary

Approval config never allows workspace escapes, unknown tools, undeclared
capabilities, or hardline terminal blocks.
