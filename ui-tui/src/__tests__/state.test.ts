import { describe, expect, it } from "vitest"
import { clearPrompt, createInitialState, reduceGatewayEvent, selectPromptChoice } from "../app/state"

describe("interaction reducer", () => {
  it("tracks core revision from ready events", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.ready",
      payload: { core_id: "assistant", core_revision: "0001", session_id: "session_1" },
    })

    expect(state.status).toMatchObject({ core_id: "assistant", core_revision: "0001", session_id: "session_1" })
  })

  it("renders user and assistant messages as separate transcript blocks", () => {
    let state = createInitialState()
    state = reduceGatewayEvent(state, { event: "interaction.message", payload: { role: "user", text: "hello" } })
    state = reduceGatewayEvent(state, {
      event: "interaction.deliver",
      payload: { deliveries: [{ kind: "message", text: "hi", visible: true, metadata: {} }] },
    })

    expect(state.transcript).toMatchObject([
      { type: "message", role: "user", text: "hello" },
      { type: "message", role: "assistant", text: "hi" },
    ])
  })

  it("keeps tool display quiet/summary/full semantics in state", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.deliver",
      payload: {
        tool_display: "full",
        tool_calls: [{ index: 1, name: "tools_list", id: "call_1", phase: "finish", status: "ok", summary: "done", arguments: {} }],
      },
    })

    expect(state.transcript[0]).toMatchObject({ type: "tool", display: "full" })
  })

  it("updates an existing tool block when a finish event arrives", () => {
    let state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.deliver",
      payload: {
        tool_display: "summary",
        tool_calls: [{ index: 1, name: "terminal", id: "call_1", phase: "start", status: "running", summary: "$ whoami" }],
      },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.deliver",
      payload: {
        tool_display: "summary",
        tool_calls: [{ index: 1, name: "terminal", id: "call_1", phase: "finish", status: "ok", summary: "$ whoami cwd: . exit_code: 0" }],
      },
    })

    expect(state.transcript).toHaveLength(1)
    expect(state.transcript[0]).toMatchObject({ type: "tool", tools: [{ id: "call_1", status: "ok", summary: "$ whoami cwd: . exit_code: 0" }] })
  })

  it("keeps progress deliveries separate from assistant messages", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.deliver",
      payload: {
        deliveries: [{ kind: "progress", text: "Running tests", visible: true, metadata: { step: "test" } }],
      },
    })

    expect(state.transcript[0]).toMatchObject({ type: "progress", text: "Running tests", metadata: { step: "test" } })
  })

  it("replaces transcript from history snapshots", () => {
    let state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.message",
      payload: { role: "user", text: "new session text" },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.history",
      payload: {
        session_id: "session_1",
        items: [
          { id: "history_1", type: "message", role: "user", text: "old question" },
          {
            id: "history_tool_1",
            type: "tool",
            display: "full",
            tools: [
              {
                index: 1,
                name: "tools_list",
                id: "call_1",
                status: "ok",
                summary: "listed tools",
                arguments: {},
                result: "listed tools",
              },
            ],
          },
          { id: "history_2", type: "message", role: "assistant", text: "old answer" },
        ],
      },
    })

    expect(state.transcript).toMatchObject([
      { type: "message", role: "user", text: "old question" },
      { type: "tool", display: "full", tools: [{ name: "tools_list", status: "ok" }] },
      { type: "message", role: "assistant", text: "old answer" },
    ])
    expect(state.transcript).not.toContainEqual(expect.objectContaining({ text: "new session text" }))
  })

  it("tracks prompt selection", () => {
    let state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.prompt.request",
      payload: { prompt_id: "prompt_1", kind: "clarify", question: "Which?", choices: ["a", "b"] },
    })
    state = selectPromptChoice(state, 1)
    expect(state.prompt).toMatchObject({ selected: 1 })
    state = clearPrompt(state)
    expect(state.prompt).toBeNull()
  })

  it("tracks approval prompts", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.approval.request",
      payload: { approval_id: "approval_1", request: { tool_name: "terminal", risk: "critical", action: "exec" } },
    })
    expect(state.prompt).toMatchObject({ type: "approval", approval_id: "approval_1", selected: 0 })
  })

  it("stores slash commands from ready payload", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.ready",
      payload: {
        slash_commands: [
          { name: "status", description: "Show runtime status", group: "Core", usage: null },
          { name: "skill", description: "View a skill", group: "Tools", usage: "/skill <name>" },
        ],
      },
    })
    expect(state.slashCommands).toEqual([
      { name: "status", description: "Show runtime status", group: "Core", usage: null },
      { name: "skill", description: "View a skill", group: "Tools", usage: "/skill <name>" },
    ])
  })

  it("stores user message alignment from ready and status payloads", () => {
    let state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.ready",
      payload: { user_message_align: "right" },
    })
    expect(state.status.user_message_align).toBe("right")

    state = reduceGatewayEvent(state, {
      event: "interaction.status",
      payload: { user_message_align: "left" },
    })
    expect(state.status.user_message_align).toBe("left")

    state = reduceGatewayEvent(state, {
      event: "interaction.status",
      payload: { user_message_align: "center" },
    })
    expect(state.status.user_message_align).toBe("left")
  })

  it("stores theme colors from ready and status payloads", () => {
    let state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.ready",
      payload: { demiurge_theme_color: "fac", user_theme_color: "#abc" },
    })
    expect(state.status.demiurge_theme_color).toBe("#ffaacc")
    expect(state.status.user_theme_color).toBe("#aabbcc")

    state = reduceGatewayEvent(state, {
      event: "interaction.status",
      payload: { demiurge_theme_color: "#ff9afc", user_theme_color: "9cc9ff" },
    })
    expect(state.status.demiurge_theme_color).toBe("#ff9afc")
    expect(state.status.user_theme_color).toBe("#9cc9ff")

    state = reduceGatewayEvent(state, {
      event: "interaction.status",
      payload: { demiurge_theme_color: "pink", user_theme_color: "#12" },
    })
    expect(state.status.demiurge_theme_color).toBe("#ff9afc")
    expect(state.status.user_theme_color).toBe("#9cc9ff")
  })
})
