# Security Policy

## Supported Versions

Demiurge is currently a `0.x` alpha project. Security support is focused on the
current development line and the latest published `0.x` GitHub Release.

| Version line | Support status |
| --- | --- |
| `main` | Supported. Security fixes land here first. |
| Latest `0.x` GitHub Release | Supported on a best-effort basis for critical fixes. Maintainers may publish a patch release when needed. |
| Older `0.x` releases | Not supported unless maintainers explicitly choose to backport a fix. |

The project does not provide long-term support branches during alpha. APIs,
runtime layout, and authored Agent Core contracts may change before `1.0`.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately to the maintainers instead of
opening a public issue with exploit details. Include:

- affected commit, tag, or package version;
- operating system and Python version;
- whether the issue affects the host, an Agent Core, a package recipe, a
  provider integration, or a channel such as Telegram;
- reproduction steps;
- expected and observed behavior;
- relevant logs with secrets removed.

Do not include API keys, bot tokens, private keys, private prompts, session
transcripts, or sensitive local file contents in reports.

## Current Threat Model

Demiurge is a local-first agent harness. It is designed to make host-owned
effects explicit and reviewable. It is not a container sandbox.

Current controls include:

- file writes, patches, and terminal working directories are scoped to the configured workspace;
- built-in file reads can target host-visible paths outside the workspace, but outside-workspace reads require approval before execution;
- sensitive reads require approval;
- writes, deletion, terminal commands, network access, and state-changing
  actions require approval by default;
- non-interactive execution without an approval provider fails closed;
- Telegram access uses numeric user/chat allowlists in the active core;
- Telegram group approvals fail closed in the current alpha line;
- Candidate Agent Core evolution cannot automatically add Python dependencies
  outside the host lock file.

Treat authored modules and installed agent packages as code that runs in the
host-shared Python environment unless a future isolation mode explicitly says
otherwise.

## Current Non-Goals

The current alpha line does not provide:

- container, VM, or OS sandboxing;
- per-core Python environments by default;
- PTY isolation;
- interactive sudo support;
- durable restart recovery for background processes;
- dependency security scanning gates;
- a guarantee that old internal runtime layouts remain compatible.

## Safe Configuration Guidelines

- Keep API keys and bot tokens in environment variables or another private
  secret store.
- Do not commit runtime homes, `.env*`, private keys, session logs, or local
  workspace data.
- Keep the default workspace narrow. The default is
  `~/.demiurge/workspace`; use `--workspace` or `DEMIURGE_WORKSPACE` for a
  specific project.
- Use Telegram numeric allowlists. Do not rely on usernames for authorization.
- Review dependency changes manually. Candidate auto-promotion must not add
  Python dependencies.
- Install package repositories only from local paths, built-in packages, or
  explicitly trusted Git/path sources.
