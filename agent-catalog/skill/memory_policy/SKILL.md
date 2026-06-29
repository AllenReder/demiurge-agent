---
name: memory_policy
description: Decide what belongs in durable local memory.
category: memory
---

# Memory Policy

Use `memory` for compact durable facts that should survive future sessions.

Save user preferences, corrections, stable profile details, environment facts,
project conventions, and tool or workflow lessons. Prefer short entries that
will remain true without the current conversation.

Do not save task progress, temporary todos, raw data dumps, commit or PR status,
or easily rediscovered facts. Reusable procedures belong in a skill, not memory.

