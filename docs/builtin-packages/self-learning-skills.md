---
title: self_learning_skills
description: Install the built-in self-learning skills package.
---

# self_learning_skills

`self_learning_skills` periodically reviews recent turns and lets a constrained
same-core child agent update the active core's skills. It is a skills-only loop;
it does not read or write memory.

The package installs:

```text
agent/lib/self_learning_skills/
agent/output/self_learning_skills/
```

The output slot runs in the parallel output pipeline. It counts completed turns
in session state, and when the configured interval is reached it calls the same
core with:

- `input_slots=["base_input"]`
- `output_slots=["base_output"]`
- `tools=["skills_list", "skill_view", "skill_manage"]`
- `use_bootstrap=True`

The child can only use the skill tools selected above. Skill writes still go
through normal `skill_manage` capability and approval checks, and changes take
effect on later turns.

## Install

Use the interactive package manager:

```bash
uv run demiurge package
```

Or install from the CLI:

```bash
uv run demiurge package install self_learning_skills --core assistant --preview
uv run demiurge package install self_learning_skills --core assistant
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `interval` | `10` | Number of turns between skill review passes. |
| `history_limit` | `40` | Number of recent history messages supplied to the review child. |
| `notify` | `true` | Emit transient notices when a review updates skills or fails. |

## Requirements

The target core must expose `skills_list`, `skill_view`, and `skill_manage`, and
must allow `skill_manage` to request `fs.write` through the host approval flow.
The package grants its output slot `agents.run:*` and scoped session-state
counter access.

Because the review uses `ctx.agents.run(...)`, the current turn waits for the
review child to finish when a review is triggered.
