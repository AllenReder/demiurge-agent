import { Box, Text } from "ink"
import type { StatusState } from "../types"
import { displayWidth, truncateMiddle } from "../lib/terminal"
import { colors, type ThemeColors } from "./theme"

export function ActivityBar(props: { colors?: ThemeColors; columns: number; now?: number; status: StatusState }) {
  if (!props.status.work_started_at) return null
  const theme = props.colors ?? colors
  const text = workingLabel(props.status, props.now ?? Date.now())
  return (
    <Box paddingX={1} width={props.columns}>
      <Text color={theme.warning}>{truncateMiddle(text, Math.max(8, props.columns - 2))}</Text>
    </Box>
  )
}

export function Footer(props: { colors?: ThemeColors; columns: number; now?: number; status: StatusState }) {
  const theme = props.colors ?? colors
  const fixedLeft = compactJoin([`${props.status.core_id}@${props.status.core_version || "?"}`, shortSession(props.status.session_id)])
  const workspaceBudget = Math.max(0, Math.floor(props.columns * 0.45) - displayWidth(fixedLeft) - 3)
  const left = compactJoin([fixedLeft, truncateMiddle(props.status.workspace, workspaceBudget)])
  const right = compactJoin([
    providerLabel(props.status),
    props.status.status,
    `tools ${props.status.tool_display}`,
    `busy ${props.status.busy_mode}`,
    `queued ${props.status.queued_inputs}`,
    `bg ${props.status.background_tasks}`,
    `messages ${props.status.message_count}`,
  ])
  const gap = 3
  const maxLeft = Math.max(displayWidth(fixedLeft), props.columns - Math.min(displayWidth(right), Math.floor(props.columns * 0.55)) - gap)
  const renderedLeft = truncateMiddle(left, maxLeft)
  const maxRight = Math.max(8, props.columns - displayWidth(renderedLeft) - gap)
  return (
    <Box marginTop={1} width={props.columns}>
      <Text color={colors.muted}>
        {renderedLeft}
        {" ".repeat(Math.max(1, props.columns - displayWidth(renderedLeft) - displayWidth(right) - 1))}
      </Text>
      <Text color={props.status.status === "running" ? theme.warning : colors.muted}>{truncateMiddle(right, maxRight)}</Text>
    </Box>
  )
}

function workingLabel(status: StatusState, now: number): string {
  const parts = [`Working ${formatElapsed(now - status.work_started_at)}`]
  if (status.activity === "waiting for model" && status.activity_started_at) {
    parts.push("Waiting for model")
  }
  return parts.join(" · ")
}

function formatElapsed(value: number): string {
  const elapsed = Math.max(0, Math.floor(value / 1000))
  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`
}

function providerLabel(status: StatusState): string {
  return status.provider || status.model ? `${status.provider || "?"}:${status.model || "?"}` : ""
}

function compactJoin(values: string[]): string {
  return values.filter(Boolean).join(" · ")
}

function shortSession(value: string): string {
  return value.length <= 10 ? value : value.slice(0, 10)
}
