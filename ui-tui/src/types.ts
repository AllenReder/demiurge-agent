export type Role = "user" | "assistant" | "system"
export type UserMessageAlign = "left" | "right"

export type TranscriptItem =
  | {
      id: string
      type: "message"
      role: Role
      text: string
      metadata?: Record<string, unknown>
    }
  | {
      id: string
      type: "tool"
      display: "quiet" | "summary" | "full"
      tools: ToolResultView[]
    }
  | {
      id: string
      type: "progress"
      text: string
      metadata?: Record<string, unknown>
    }
  | {
      id: string
      type: "notice"
      text: string
      level?: string
    }

export type ToolResultView = {
  index: number
  name: string
  id: string
  status: "ok" | "error"
  summary: string
  arguments?: unknown
  result?: string
  model_output?: string | null
}

export type StatusState = {
  workspace: string
  core_id: string
  core_version: string
  session_id: string
  provider: string
  model: string
  runtime_timezone: string
  runtime_timezone_source: string
  status: "idle" | "running" | string
  tool_display: "quiet" | "summary" | "full"
  user_message_align: UserMessageAlign
  demiurge_theme_color: string
  user_theme_color: string
  busy_mode: "interrupt" | "queue"
  queued_inputs: number
  background_tasks: number
  message_count: number
  pending_prompts: number
  pending_approvals: number
  last_error: string
}

export type PromptPanel =
  | {
      type: "prompt"
      prompt_id: string
      kind: "clarify" | "resume" | string
      question: string
      choices: string[]
      records?: SessionRecord[]
      selected: number
    }
  | {
      type: "approval"
      approval_id: string
      request: ApprovalRequest
      selected: number
      showFull: boolean
    }

export type ApprovalRequest = {
  tool_name: string
  tool_call_id: string
  turn_id: string
  capability: string
  action: string
  risk: string
  summary: string
  target?: string | null
  command?: string | null
  arguments_preview?: Record<string, unknown>
}

export type SessionRecord = {
  session_id: string
  title?: string | null
  updated_at: string
  channel?: string | null
  message_count: number
  preview?: string | null
}

export type SlashCommandSpec = {
  name: string
  description: string
  group: string
  usage?: string | null
}

export type AppState = {
  ready: boolean
  transcript: TranscriptItem[]
  prompt: PromptPanel | null
  status: StatusState
  slashCommands: SlashCommandSpec[]
}

export type GatewayEvent = {
  event: string
  payload: Record<string, unknown>
}

export type RpcResponse = {
  id: number | string
  result?: unknown
  error?: { code?: string; message: string }
}

export type RpcFrame = GatewayEvent | RpcResponse
