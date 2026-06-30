# Sessions and Context

Sessions are host-managed. Agent cores do not save history, assemble provider
context, or compact transcripts directly.

## Storage

Each session lives under:

```text
~/.demiurge/sessions/<session_id>/
  session.json
  bootstrap_context.md
  messages.jsonl
  events.jsonl
  artifacts/
```

| File | Purpose |
| --- | --- |
| `session.json` | Core, channel, conversation key, workspace, provider/model snapshot, counts, compaction state. |
| `bootstrap_context.md` | Session-stable context produced by `agent/bootstrap/`; existence marks bootstrap as complete. |
| `messages.jsonl` | Transcript records for user messages, assistant tool calls, tool results, and deliveries. |
| `events.jsonl` | Turn, step, tool, approval, context assembly, delivery, and compaction events. |
| `artifacts/` | Session-scoped files registered by delivery calls. |

## Context Assembly Order

Before each model call, the host assembles context in this order:

1. Core soul.
2. Skill index.
3. Bootstrap context.
4. Input module placements.
5. Compaction summary.
6. Session history tail.
7. Current turn messages and tool results.

Each assembly writes a `context.assembled` event. `/trace` shows layer counts.
If local host config enables `debug.show_system_prompt`, the host also sends the
assembled system messages to the active channel before each provider call. That
debug delivery is transient: it is visible in the channel but is not written to
`messages.jsonl` and is not model-visible later.

## History Policy

Delivery calls use `history_policy` to decide what enters `messages.jsonl` and
future model context.

| Policy | Written to `messages.jsonl` | Model-visible later |
| --- | --- | --- |
| `persist` | Yes | Yes |
| `model_hidden` | Yes | No |
| `transient` | No | No |

See [../reference/history-policy-and-delivery.md](../reference/history-policy-and-delivery.md).

## Resume

CLI:

```bash
uv run demiurge --resume <session_id>
uv run demiurge --session <session_id>
```

TUI:

```text
/sessions
/resume
/new
```

TUI uses the current local session. Telegram resolves durable sessions by
`telegram:<chat_id>` plus core id.

## Manual Compaction

```text
/compact
/compact focus text
```

Manual compaction asks the current provider to summarize older history and keeps
recent complete turns. The summary is reference-only historical context, not the
current task.

## Current Limits

The current implementation does not provide automatic threshold compaction,
SQLite/FTS search, session trees, or exact token budgeting.
