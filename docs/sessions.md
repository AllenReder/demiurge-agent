# Sessions and Context

Sessions are host-managed. Agent cores do not save history, assemble context,
or run compaction directly.

## Storage

Each session lives under:

```text
~/.demiurge/sessions/<session_id>/
  session.json
  messages.jsonl
  events.jsonl
  artifacts/
```

- `session.json`: metadata such as core, channel, conversation key, workspace,
  provider/model snapshot, message count, and compaction state.
- `messages.jsonl`: transcript records, including user messages, assistant
  tool-call steps, tool results, and input/output module deliveries.
- `events.jsonl`: turn, step, tool, approval, context assembly, delivery, and
  compaction events.
- `artifacts/`: session-scoped files registered by `ctx.input/ctx.output`
  delivery.

Assistant tool-call steps and tool results are written by runner-owned
`RuntimeIO` so later provider requests can reconstruct valid tool-call/result
pairs. Tool results are normally `visible=false` but `model_visible=true`.

Child core deliveries stay in the child session. A parent core must read
`ctx.agents.run(...).result` and explicitly deliver any child artifact through
the parent's own phase facade.

## History Policy

Phase-local `send_*` calls use `history_policy`:

| Policy | Written to `messages.jsonl` | `model_visible` | Enters later model context |
| --- | --- | --- | --- |
| `persist` | yes | `true` | yes |
| `model_hidden` | yes | `false` | no |
| `transient` | no | n/a | no |

`messages.jsonl` is the session transcript, not necessarily the user-visible
chat log. Later prompts only read message records with `model_visible=true` and
restore assistant `tool_calls` plus `tool` messages.

Use `transient` for progress, notices, and debug status. Use `model_hidden` for
user-visible background results that should not influence later prompts.

## Resume

Resume by CLI:

```bash
uv run demiurge --resume <session_id>
uv run demiurge --session <session_id>
```

In the TUI:

```text
/sessions
/resume
/new
```

`/sessions` lists recent sessions, `/resume` switches to an existing session,
and `/new` creates a new local session.

## Interaction Routing

The host resolves durable sessions by `channel + conversation_key + core_id`.

- TUI uses the current local session.
- Telegram uses `telegram:<chat_id>` as the conversation key, so the same chat
  continues the same session.

Channels adapt platform input/output only. The Interaction Bridge passes
normalized input to the host runner and routes typed delivery, prompts, and
approval requests back to the channel.

## Context Assembly

Before each model call, the host assembles context in a fixed order:

1. core soul;
2. skill index;
3. input placements;
4. compaction summary;
5. session history tail;
6. current turn messages and tool results.

Each assembly writes a `context.assembled` event. `/trace` shows layer counts.

## Manual Compaction

In the TUI:

```text
/compact
/compact focus text
```

`/compact` asks the current provider to summarize older history, keeps recent
complete turns, and stores a reference-only `compaction_summary`. The summary
is historical context, not the current task.

The current implementation does not provide automatic threshold compaction,
SQLite/FTS search, session trees, or exact token budgeting.
