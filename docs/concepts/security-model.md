# Security Model

demiurge's current safety layer is application-level control. It is not a
container sandbox and does not provide per-core Python environments by default.

## Workspace Scope

File and terminal tools can only access the resolved workspace.

Resolution:

1. CLI `--workspace`.
2. `DEMIURGE_WORKSPACE`.
3. Local TUI launch directory.
4. For gateway/scheduler, concrete core `runtime.workspace`.
5. `~/.demiurge/workspace`.

Paths outside the workspace are rejected before tool execution.

## Sensitive Paths

These paths or file types are sensitive by default:

- `.env*`
- `.ssh/`
- private keys
- `.git/`
- `.venv/`
- `.demiurge/`
- writes to `pyproject.toml` or `uv.lock`

Sensitive reads require approval even when inside the workspace.

## Approval Policy

Default behavior:

- Ordinary read-only workspace access is allowed.
- Sensitive reads require approval.
- Writes, deletion, network access, and state-changing actions require approval.
- Terminal commands pass through a host-owned command guard.
- Non-interactive execution without an approval provider fails closed.

Interactive approvals:

- TUI uses a local modal with allow-once, allow-for-session, and deny choices.
- Telegram private chats use inline `Allow once`, `Allow for session`, and
  `Deny` buttons.
- Telegram group chats do not currently support interactive approvals;
  approval-required actions fail closed.

## Channel Trust

Telegram is deny-by-default:

- Private chats require sender `from.id` in `allowed_users`.
- Groups require both sender `from.id` and `chat.id` to be allowed.
- Unauthorized callbacks do not resolve `clarify` choices or approvals.

## Configuration

Global fallback policy can be set in `~/.demiurge/agents/agent.yaml`:

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

Concrete cores can make policy stricter, but cannot lower the host security
baseline into unconditional allow.

## Non-Goals

The current implementation does not provide:

- container sandboxing;
- PTY isolation;
- interactive sudo support;
- background process restart recovery;
- per-core Python environments by default.
