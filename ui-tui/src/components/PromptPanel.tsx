import { Box, Text } from "ink"
import type { PromptPanel as PromptPanelState } from "../types"
import { colors, type ThemeColors } from "./theme"

export function PromptPanel(props: { colors?: ThemeColors; columns: number; prompt: PromptPanelState }) {
  const theme = props.colors ?? colors
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={theme.warning}>{"─".repeat(Math.max(1, props.columns - 1))}</Text>
      <Box flexDirection="column" paddingX={1}>
        {props.prompt.type === "approval" ? <ApprovalPrompt colors={theme} prompt={props.prompt} /> : <QuestionPrompt colors={theme} prompt={props.prompt} />}
      </Box>
    </Box>
  )
}

function QuestionPrompt(props: { colors: ThemeColors; prompt: Extract<PromptPanelState, { type: "prompt" }> }) {
  const choices =
    props.prompt.kind === "resume" && props.prompt.records?.length
      ? props.prompt.records.map((record, index) => `${index + 1}. ${shortSession(record.session_id)} · ${record.message_count} · ${record.preview ?? ""}`)
      : props.prompt.choices.map((choice, index) => `${index + 1}. ${choice}`)
  return (
    <Box flexDirection="column">
      <Text color={props.colors.warning} bold>
        {props.prompt.kind === "resume" ? "Resume session" : "Question"}
      </Text>
      <Text color={colors.text} wrap="wrap">
        {props.prompt.question}
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {choices.map((choice, index) => (
          <Text key={choice} color={index === props.prompt.selected ? colors.selected : colors.muted} bold={index === props.prompt.selected}>
            {index === props.prompt.selected ? "›" : " "} {choice}
          </Text>
        ))}
      </Box>
      <Text color={colors.muted}>Enter selects · Up/Down moves · Esc cancels</Text>
    </Box>
  )
}

function ApprovalPrompt(props: { colors: ThemeColors; prompt: Extract<PromptPanelState, { type: "approval" }> }) {
  const choices = ["allow once", "allow for session", "deny"]
  const request = props.prompt.request
  const command = props.prompt.showFull ? (request.command ?? "") : shorten(request.command ?? "", 140)
  return (
    <Box flexDirection="column">
      <Text color={props.colors.warning} bold>
        Approval required
      </Text>
      <Text color={colors.text}>
        {request.tool_name} <Text color={colors.muted}>· risk {request.risk} · {request.action}</Text>
      </Text>
      <Text color={colors.muted} wrap="wrap">
        {request.summary}
      </Text>
      {command ? (
        <Text color={colors.muted} wrap="wrap">
          command: {command}
        </Text>
      ) : null}
      <Box flexDirection="column" marginTop={1}>
        {choices.map((choice, index) => (
          <Text key={choice} color={index === props.prompt.selected ? colors.selected : colors.muted} bold={index === props.prompt.selected}>
            {index === props.prompt.selected ? "›" : " "} {index + 1}. {choice}
          </Text>
        ))}
      </Box>
      <Text color={colors.muted}>Enter selects · Up/Down moves · f toggles command · Esc denies</Text>
    </Box>
  )
}

function shortSession(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 10)}..${value.slice(-6)}`
}

function shorten(value: string, limit: number): string {
  if (value.length <= limit) return value
  return `${value.slice(0, limit - 3)}...`
}
