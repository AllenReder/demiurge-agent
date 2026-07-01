import type {
  AppState,
  ApprovalRequest,
  GatewayEvent,
  PromptPanel,
  SessionRecord,
  SlashCommandSpec,
  StatusState,
  ToolResultView,
  TranscriptItem,
  UserMessageAlign,
} from "../types"

export const initialStatus: StatusState = {
  workspace: "",
  core_id: "assistant",
  core_version: "",
  session_id: "",
  provider: "",
  model: "",
  runtime_timezone: "",
  runtime_timezone_source: "",
  status: "idle",
  tool_display: "summary",
  user_message_align: "left",
  demiurge_theme_color: "#ff9afc",
  user_theme_color: "#9cc9ff",
  busy_mode: "interrupt",
  queued_inputs: 0,
  background_tasks: 0,
  message_count: 0,
  pending_prompts: 0,
  pending_approvals: 0,
  last_error: "",
}

export function createInitialState(): AppState {
  return {
    ready: false,
    transcript: [],
    prompt: null,
    status: initialStatus,
    slashCommands: [],
  }
}

export function reduceGatewayEvent(state: AppState, frame: GatewayEvent): AppState {
  const event = frame.event
  const payload = frame.payload
  if (event === "interaction.ready") {
    return {
      ...state,
      ready: true,
      status: {
        ...state.status,
        workspace: stringValue(payload.workspace),
        core_id: stringValue(payload.core_id) || state.status.core_id,
        session_id: stringValue(payload.session_id),
        provider: stringValue(payload.provider),
        model: stringValue(payload.model),
        runtime_timezone: stringValue(payload.runtime_timezone),
        runtime_timezone_source: stringValue(payload.runtime_timezone_source),
        tool_display: toolDisplayValue(payload.tool_display),
        user_message_align: userMessageAlignValue(payload.user_message_align),
        demiurge_theme_color: hexColorValue(payload.demiurge_theme_color, state.status.demiurge_theme_color),
        user_theme_color: hexColorValue(payload.user_theme_color, state.status.user_theme_color),
        busy_mode: busyModeValue(payload.busy_mode),
      },
      slashCommands: slashCommandsFromPayload(payload.slash_commands),
    }
  }
  if (event === "interaction.status") {
    return { ...state, status: { ...state.status, ...statusFromPayload(payload) } }
  }
  if (event === "interaction.message") {
    const role = stringValue(payload.role) === "user" ? "user" : "system"
    return appendItem(state, {
      id: nextId(state, "message"),
      type: "message",
      role,
      text: stringValue(payload.text),
    })
  }
  if (event === "interaction.deliver") {
    let next = state
    const tools = arrayValue(payload.tool_results) as ToolResultView[]
    if (tools.length) {
      next = appendItem(next, {
        id: nextId(next, "tool"),
        type: "tool",
        display: toolDisplayValue(payload.tool_display),
        tools,
      })
    }
    for (const delivery of arrayValue(payload.deliveries)) {
      if (!recordValue(delivery)?.visible && recordValue(delivery)?.visible !== undefined) continue
      const record = recordValue(delivery)
      if (!record) continue
      const text = stringValue(record.text) || stringValue(record.fallback_text)
      if (!text) continue
      const kind = stringValue(record.kind)
      const metadata = recordValue(record.metadata) ?? {}
      if (kind === "progress") {
        next = appendItem(next, {
          id: nextId(next, "progress"),
          type: "progress",
          text,
          metadata,
        })
      } else if (kind === "notice" || stringValue(metadata.level)) {
        next = appendItem(next, {
          id: nextId(next, "notice"),
          type: "notice",
          text,
          level: stringValue(metadata.level) || "info",
        })
      } else {
        const role = stringValue(metadata.role) === "system" ? "system" : "assistant"
        next = appendItem(next, {
          id: nextId(next, "message"),
          type: "message",
          role,
          text,
          metadata,
        })
      }
    }
    return next
  }
  if (event === "interaction.prompt.request") {
    const prompt: PromptPanel = {
      type: "prompt",
      prompt_id: stringValue(payload.prompt_id),
      kind: stringValue(payload.kind) || "clarify",
      question: stringValue(payload.question),
      choices: arrayValue(payload.choices).map(String),
      records: arrayValue(payload.records) as SessionRecord[],
      selected: 0,
    }
    return { ...state, prompt }
  }
  if (event === "interaction.approval.request") {
    const request = (recordValue(payload.request) ?? {}) as ApprovalRequest
    return {
      ...state,
      prompt: {
        type: "approval",
        approval_id: stringValue(payload.approval_id),
        request,
        selected: 0,
        showFull: false,
      },
    }
  }
  if (event === "interaction.error") {
    const message = stringValue(payload.message)
    return appendItem(
      { ...state, status: { ...state.status, last_error: message } },
      { id: nextId(state, "notice"), type: "notice", text: message, level: "error" },
    )
  }
  if (event === "channel.shutdown") {
    return { ...state, status: { ...state.status, status: "idle" } }
  }
  return state
}

export function selectPromptChoice(state: AppState, delta: number): AppState {
  if (!state.prompt) return state
  const count = state.prompt.type === "approval" ? 3 : Math.max(1, state.prompt.choices.length || state.prompt.records?.length || 1)
  return {
    ...state,
    prompt: {
      ...state.prompt,
      selected: (state.prompt.selected + delta + count) % count,
    },
  }
}

export function clearPrompt(state: AppState): AppState {
  return { ...state, prompt: null }
}

export function toggleApprovalCommand(state: AppState): AppState {
  if (state.prompt?.type !== "approval") return state
  return { ...state, prompt: { ...state.prompt, showFull: !state.prompt.showFull } }
}

function appendItem(state: AppState, item: TranscriptItem): AppState {
  return { ...state, transcript: [...state.transcript, item].slice(-500) }
}

function nextId(state: AppState, prefix: string): string {
  return `${prefix}_${state.transcript.length + 1}`
}

function statusFromPayload(payload: Record<string, unknown>): Partial<StatusState> {
  return {
    workspace: stringValue(payload.workspace),
    core_id: stringValue(payload.core_id),
    core_version: stringValue(payload.core_version),
    session_id: stringValue(payload.session_id),
    provider: stringValue(payload.provider),
    model: stringValue(payload.model),
    status: stringValue(payload.status) || "idle",
    tool_display: toolDisplayValue(payload.tool_display),
    user_message_align: userMessageAlignValue(payload.user_message_align),
    demiurge_theme_color: hexColorValue(payload.demiurge_theme_color, initialStatus.demiurge_theme_color),
    user_theme_color: hexColorValue(payload.user_theme_color, initialStatus.user_theme_color),
    busy_mode: busyModeValue(payload.busy_mode),
    queued_inputs: numberValue(payload.queued_inputs),
    background_tasks: numberValue(payload.background_tasks),
    message_count: numberValue(payload.message_count),
    pending_prompts: numberValue(payload.pending_prompts),
    pending_approvals: numberValue(payload.pending_approvals),
    last_error: stringValue(payload.last_error),
  }
}

function toolDisplayValue(value: unknown): "quiet" | "summary" | "full" {
  return value === "quiet" || value === "full" ? value : "summary"
}

function busyModeValue(value: unknown): "interrupt" | "queue" {
  return value === "queue" ? "queue" : "interrupt"
}

function userMessageAlignValue(value: unknown): UserMessageAlign {
  return value === "right" ? "right" : "left"
}

function hexColorValue(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback
  let raw = value.trim().toLowerCase()
  if (raw.startsWith("#")) raw = raw.slice(1)
  if (/^[0-9a-f]{3}$/.test(raw)) raw = raw.replace(/./g, (char) => char + char)
  return /^[0-9a-f]{6}$/.test(raw) ? `#${raw}` : fallback
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value)
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
}

function slashCommandsFromPayload(value: unknown): SlashCommandSpec[] {
  return arrayValue(value)
    .map(recordValue)
    .filter((record): record is Record<string, unknown> => Boolean(record))
    .map((record) => ({
      name: stringValue(record.name),
      description: stringValue(record.description),
      group: stringValue(record.group),
      usage: stringValue(record.usage) || null,
    }))
    .filter((command) => command.name)
}
