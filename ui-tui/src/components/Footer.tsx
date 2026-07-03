import { Box, Text } from "ink"
import type { StatusState } from "../types"
import { displayWidth, truncateMiddle } from "../lib/terminal"
import { colors, type ThemeColors } from "./theme"

export function Footer(props: { colors?: ThemeColors; columns: number; status: StatusState }) {
  const theme = props.colors ?? colors
  const fixedLeft = compactJoin([props.status.core_id, shortSession(props.status.session_id)])
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

function providerLabel(status: StatusState): string {
  return status.provider || status.model ? `${status.provider || "?"}:${status.model || "?"}` : ""
}

function compactJoin(values: string[]): string {
  return values.filter(Boolean).join(" · ")
}

function shortSession(value: string): string {
  const prefix = "session_"
  if (!value.startsWith(prefix)) return value.length <= 10 ? value : value.slice(-10)
  const suffix = value.slice(prefix.length)
  return suffix.length <= 10 ? value : `s:${suffix.slice(-8)}`
}
