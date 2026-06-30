# Testing Agent Cores

Use these checks after editing a runtime core, installing a package, or changing
source templates.

## Structural Checks

```bash
uv run demiurge init --check
uv run demiurge doctor
```

These checks catch missing templates, stale runtime/source drift, invalid YAML,
unknown toolsets, missing pipelines, and unknown pipeline slot ids.

## Local Runtime Check

```bash
uv run demiurge --provider fake
```

Use `/status`, `/tools`, `/events`, and `/sessions` to confirm the active core,
visible tools, event log, and session records.

## Module Behavior Check

After one prompt:

```bash
tail -n 50 ~/.demiurge/sessions/<session_id>/events.jsonl
tail -n 50 ~/.demiurge/sessions/<session_id>/messages.jsonl
```

Check that:

- input modules added the intended current-turn content;
- output modules delivered expected messages or artifacts;
- `transient` deliveries are not in `messages.jsonl`;
- `model_hidden` deliveries are stored but not model-visible;
- tool results remain model-visible as `role="tool"` entries.

## Telegram Check

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

Telegram only listens when enabled in the concrete core. Private chats require
numeric sender ids in `allowed_users`; groups also require `allowed_chats`.

## Schedule Check

Use a near-future cron expression and inspect:

```bash
tail -n 20 ~/.demiurge/scheduler/<core_id>/runs.jsonl
```

## Boundary

Do not run broad formatters for doc-only or core-authoring checks unless the
change requires it. Structural checks and fake-provider runtime checks are the
fastest confidence path.
