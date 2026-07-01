---
title: Sessions and Context
description: Understand how turns become provider context and durable session records.
---

# Sessions and Context

A Demiurge run is organized as sessions, turns, and steps.

- A **session** is the durable conversation container.
- A **turn** is one inbound user, channel, schedule, or tool-triggered action.
- A **step** is part of the model/tool loop inside a turn.

The host owns this structure.

## Context Assembly

Provider context is assembled from several sources:

1. core soul and runtime instructions
2. skill index and loaded skills
3. bootstrap context
4. input module placements
5. session history according to history policy
6. current turn input

Input modules can add current-turn content, but the host decides the final
provider message shape.

## Bootstrap Context

Bootstrap modules run at session start and can provide stable context such as
memory notes or continuity summaries. They should treat stored facts as
reference material and avoid pretending stale context is authoritative.

## History Policy

Output modules and delivery calls can choose whether delivered content is
persisted in session history. Persisted content can appear in later context.
Transient notices and progress messages can be delivered live without becoming
part of durable assistant history.

## Resume and Compaction

Sessions can be resumed by id. Manual compaction can summarize long sessions
when the runtime supports it for the current path. The durable session store,
not the Agent Core, owns history persistence.

## Boundary

Agent Cores may shape inputs and outputs. They do not own session storage,
history replay rules, provider message construction, or context-budget policy.
