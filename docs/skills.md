# Skills

demiurge skills use progressive loading. The model initially sees a lightweight
skill index and can load full skill content only when needed. This avoids
injecting every skill document into every prompt.

## Location

Skills live under an agent core's `agent/skills/` directory. Two forms are
supported:

```text
agent/skills/
  debugging.md
  research/
    SKILL.md
    references/
    templates/
    scripts/
```

Flat Markdown files are useful for single-file instructions. Packaged skills
are useful when a skill has references, templates, scripts, or assets.

## Metadata

Recommended frontmatter:

```yaml
---
name: research
description: Research material and produce a structured summary.
category: writing
---
```

Parsing rules:

1. `description` comes from frontmatter when present.
2. Without `description`, the first meaningful body line is used.
3. `category` comes from frontmatter when present. Otherwise flat skills use
   `general`, and packaged skills use their parent path or `general`.

## Progressive Loading

### Tier 1: Index

The host injects a lightweight skill index into the system prompt. It contains
name, category, and description only.

The model can also call:

```json
{ "name": "skills_list", "arguments": {} }
```

`skills_list` returns available skill metadata: `success`, `skills`,
`categories`, `count`, and `hint`.

### Tier 2: Main Document

When a skill is relevant, the model should call:

```json
{ "name": "skill_view", "arguments": { "name": "debugging" } }
```

The host injects the full `SKILL.md` or flat Markdown content as tool model
output, and records a `visible=false` tool transcript entry in session history.
Skill content does not grant new tool permissions.

### Tier 3: Linked Files

Packaged skills can expose read-only linked files under:

```text
references/
templates/
scripts/
```

The model can read discovered files with:

```json
{ "name": "skill_view", "arguments": { "name": "debugging", "file_path": "references/checklist.md" } }
```

`file_path` must be relative and must refer to a discovered linked file in that
skill package. Absolute paths, `..`, undiscovered files, and symlink escapes are
rejected.

Scripts and templates are readable content or path hints only. Execution still
goes through normal tools, capabilities, and approvals.
