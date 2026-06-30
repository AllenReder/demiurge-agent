# Skills

Skills are progressive knowledge documents owned by an agent core. They live
under `agent/skills/` and are loaded on demand with `skills_list` and
`skill_view`.

## Location

Supported layouts:

```text
agent/skills/my_skill.md
agent/skills/my-skill/SKILL.md
agent/skills/my-skill/references/details.md
agent/skills/my-skill/templates/example.md
```

The default assistant starts with an empty skill directory.

## Minimal Skill

```markdown
---
name: repo-summary
description: Summarize repository structure and entry points.
category: software-development
---

# Repo Summary

## When to Use

Use when the user asks for a fast orientation to a codebase.

## Procedure

1. Inspect the top-level files.
2. Read the README and package metadata.
3. Identify entry points, tests, and common commands.

## Verification

Mention exact files and commands inspected.
```

## Progressive Loading

The host exposes three levels:

| Level | Tool | Purpose |
| --- | --- | --- |
| Index | `skills_list` | Lightweight metadata for available skills. |
| Main document | `skill_view(name)` | Full `SKILL.md` or markdown skill body. |
| Linked file | `skill_view(name, file_path)` | Specific file under references/templates/scripts/assets. |

The model sees only the index until it explicitly loads a skill.

## Success Check

```bash
uv run demiurge --provider fake
```

Ask what skills are available or use `/tools` to confirm `skills_list` and
`skill_view` are visible when the core enables the coding toolset.

## Boundary

Loading a skill grants instructions, not new permissions. File writes, network
access, terminal commands, and tool calls still go through host-owned
capabilities and approvals.
