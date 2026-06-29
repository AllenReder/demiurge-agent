# Security Policy

## Supported Versions

demiurge is in early v1 development. Security fixes target the current `main`
branch until versioned releases are established.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately to the maintainers instead of
opening a public issue with exploit details. Include:

- affected commit or version;
- operating system and Python version;
- reproduction steps;
- expected and observed behavior;
- any relevant logs with secrets removed.

Do not include API keys, bot tokens, private keys, or sensitive local file
contents in reports.

## Current Threat Model

demiurge is a local-first agent harness. It is designed to make host-owned
effects explicit and reviewable, not to provide a container sandbox.

Current controls include:

- file and terminal tools are scoped to the configured workspace;
- paths outside the workspace are rejected before tool execution;
- sensitive reads require approval;
- writes, deletion, terminal commands, network access, and state-changing
  actions require approval by default;
- non-interactive execution without an approval provider fails closed;
- Telegram access uses numeric user/chat allowlists in the active core;
- Telegram group approvals fail closed in v1.

## Non-Goals in v1

v1 does not provide:

- container, VM, or OS sandboxing;
- per-core Python environments by default;
- PTY isolation;
- interactive sudo support;
- durable restart recovery for background processes;
- dependency security scanning gates.

Treat authored modules and installed agent packages as code that runs in the
host-shared Python environment unless a future isolation mode explicitly says
otherwise.

## Safe Configuration Guidelines

- Keep API keys and bot tokens in environment variables.
- Do not commit runtime homes, `.env*`, private keys, session logs, or local
  workspace data.
- Keep the default workspace narrow. The default is
  `~/.demiurge/workspace`; use `--workspace` or `DEMIURGE_WORKSPACE` for a
  specific project.
- Use Telegram numeric allowlists. Do not rely on usernames for authorization.
- Review dependency changes manually. Candidate auto-promotion must not add
  Python dependencies.
