# Security and Approvals

This page describes demiurge's current local safety model. It is an application-level control layer, not a container sandbox.

## Workspace Scope

File and terminal tools can only access paths inside the configured workspace. The default workspace is:

```text
~/.demiurge/workspace
```

Override it with `--workspace`, `DEMIURGE_WORKSPACE`, or `runtime.workspace` in `<home>/config.yaml`.

Paths outside the workspace are rejected by the host before tool execution.

## Sensitive Paths

The approval layer treats these locations or files as sensitive by default:

- `.env*`
- `.ssh/`
- private keys
- `.git/`
- `.venv/`
- `.demiurge/`
- writes to `pyproject.toml` or `uv.lock`

Sensitive reads require approval even when they are inside the workspace.

## Approval Policy

Default behavior:

- Ordinary read-only workspace access is allowed.
- Sensitive reads require approval.
- Writes, deletion, terminal commands, network access, and state-changing actions require approval.
- Non-interactive execution without an approval provider fails closed.

Approval events are written to the session event log.

Interactive approvals are provided by the current interaction bridge:

- TUI uses a local modal with allow-once, allow-for-session, and deny choices.
- Telegram private chats use MarkdownV2 approval messages with inline `Allow once`, `Allow for session`, and `Deny` buttons.
- Telegram approvals pause the current turn until resolved or timed out.
- Telegram group chats do not currently support interactive approvals; approval-required actions fail closed.

## Telegram Access Policy

Telegram has a separate core-local access policy under `channels.telegram`.

- Private chats require the sender's numeric `from.id` in `allowed_users`.
- Groups and supergroups require both the sender `from.id` and the numeric `chat.id` to be allowed.
- Without an allowlist, Telegram rejects all messages and callbacks.
- Unauthorized callbacks do not consume `clarify` choices and do not resolve pending approvals.

## Configuration

Global fallback config at `~/.demiurge/agents/agent.yaml` can set approval policy:

```yaml
approval:
  default: prompt
  tools:
    terminal: deny
  capabilities:
    network.fetch: prompt
  risks:
    critical: deny
```

Agent cores can declare stricter per-agent policy, but they cannot lower the host security baseline into unconditional allow.

Regardless of config, the host always enforces:

- workspace boundary checks;
- declared capability requirements;
- enabled-tool checks;
- candidate evolution scope;
- dependency-file gates;
- self-evolution write boundaries.

## Current Non-Goals

The current implementation does not provide:

- container sandboxing;
- PTY isolation;
- interactive sudo support;
- background process restart recovery;
- per-core Python environments by default.
