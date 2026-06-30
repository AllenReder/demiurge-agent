import { describe, expect, it } from "vitest"
import { clearPrompt, createInitialState, reduceGatewayEvent, selectPromptChoice } from "../app/state"

describe("interaction reducer", () => {
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
        tool_results: [{ index: 1, name: "tools_list", id: "call_1", status: "ok", summary: "done", arguments: {} }],
      },
    })

    expect(state.transcript[0]).toMatchObject({ type: "tool", display: "full" })
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

  it("merges streamed message part deltas and deduplicates matching final delivery", () => {
    let state = createInitialState()
    state = reduceGatewayEvent(state, {
      event: "interaction.message.part.updated",
      payload: {
        turn_id: "turn_1",
        message_id: "msg_1",
        part: { id: "part_1", message_id: "msg_1", turn_id: "turn_1", type: "text", text: "", metadata: { status: "streaming" } },
      },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.message.part.delta",
      payload: { turn_id: "turn_1", message_id: "msg_1", part_id: "part_1", field: "text", delta: "hel" },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.message.part.delta",
      payload: { turn_id: "turn_1", message_id: "msg_1", part_id: "part_1", field: "text", delta: "lo" },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.message.part.updated",
      payload: {
        turn_id: "turn_1",
        message_id: "msg_1",
        part: { id: "part_1", message_id: "msg_1", turn_id: "turn_1", type: "text", text: "hello", metadata: { status: "complete" } },
      },
    })
    state = reduceGatewayEvent(state, {
      event: "interaction.deliver",
      payload: { turn_id: "turn_1", deliveries: [{ kind: "message", text: "hello", visible: true, metadata: {} }] },
    })

    expect(state.transcript).toHaveLength(1)
    expect(state.transcript[0]).toMatchObject({ type: "message", role: "assistant", text: "hello", stream_status: "complete" })
  })

  it("ignores orphan streamed deltas", () => {
    const state = reduceGatewayEvent(createInitialState(), {
      event: "interaction.message.part.delta",
      payload: { turn_id: "turn_1", message_id: "msg_1", part_id: "missing", field: "text", delta: "ignored" },
    })

    expect(state.transcript).toEqual([])
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
