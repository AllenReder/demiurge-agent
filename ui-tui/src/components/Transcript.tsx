import { Box, Text } from "ink"
import type { Role, ToolResultView, TranscriptItem, UserMessageAlign } from "../types"
import { displayWidth, truncateEnd } from "../lib/terminal"
import { Markdown } from "./Markdown"
import { colors, type ThemeColors } from "./theme"

export function Transcript(props: { colors?: ThemeColors; columns: number; items: TranscriptItem[]; userMessageAlign?: UserMessageAlign }) {
  const visible = props.items.slice(-100)
  const theme = props.colors ?? colors
  return (
    <Box flexDirection="column" flexGrow={1} paddingX={1}>
      {visible.map((item, index) => {
        const previous = visible[index - 1]
        const gap = previous && group(previous) !== group(item)
        if (item.type === "message")
          return (
            <MessageBlock
              colors={theme}
              columns={props.columns - 2}
              gap={Boolean(gap)}
              key={item.id}
              role={item.role}
              text={item.text}
              userMessageAlign={props.userMessageAlign}
            />
          )
        if (item.type === "tool")
          return <ToolBlock colors={theme} columns={props.columns - 6} display={item.display} gap={Boolean(gap)} key={item.id} tools={item.tools} />
        if (item.type === "progress") return <ProgressBlock colors={theme} columns={props.columns - 6} key={item.id} text={item.text} />
        return <NoticeBlock colors={theme} gap={Boolean(gap)} key={item.id} text={item.text} level={item.level} />
      })}
    </Box>
  )
}

export function MessageBlock(props: { colors?: ThemeColors; columns?: number; gap?: boolean; role: Role; text: string; userMessageAlign?: UserMessageAlign }) {
  const columns = Math.max(32, props.columns ?? 100)
  const theme = props.colors ?? colors
  if (props.role === "user") return <UserMessage align={props.userMessageAlign ?? "left"} colors={theme} columns={columns} gap={props.gap} text={props.text} />
  return <AssistantMessage colors={theme} columns={columns} gap={props.gap} role={props.role} text={props.text} />
}

function UserMessage(props: { align: UserMessageAlign; colors: ThemeColors; columns: number; gap?: boolean; text: string }) {
  const width =
    props.align === "left"
      ? Math.max(1, props.columns - 3)
      : Math.max(18, Math.min(64, Math.floor(props.columns * 0.82), Math.max(12, props.columns - 3), Math.max(12, longestLineWidth(props.text) + 4)))
  const bubble = (
    <Box backgroundColor={props.colors.userBubble} flexDirection="column" paddingX={1} width={width}>
      <Text> </Text>
      <Text color={props.colors.text} wrap="wrap">
        {props.text}
      </Text>
      <Text> </Text>
    </Box>
  )
  return (
    <Box flexDirection="column" marginBottom={1}>
      <MessageLabel align={props.align} columns={props.columns} color={props.colors.user} label="you" side={props.align} width={width} />
      <Box justifyContent={props.align === "right" ? "flex-end" : "flex-start"} width={props.columns}>
        <Box flexDirection="row">
          {props.align === "left" ? <GutterStrip color={props.colors.userGutter} side="left" /> : null}
          {bubble}
          {props.align === "right" ? <GutterStrip color={props.colors.userGutter} side="right" /> : null}
        </Box>
      </Box>
    </Box>
  )
}

function AssistantMessage(props: { colors: ThemeColors; columns: number; gap?: boolean; role: Role; text: string }) {
  const accent = props.role === "assistant" ? props.colors.assistant : props.colors.system
  const label = props.role === "assistant" ? "demiurge" : "system"
  const bodyColumns = Math.max(1, props.columns - 3)
  return (
    <Box flexDirection="column" marginBottom={1}>
      <MessageLabel align="left" columns={props.columns} color={accent} label={label} side="left" width={bodyColumns} />
      <Box flexDirection="row">
        <GutterStrip color={accent} />
        <Box backgroundColor={props.colors.assistantBlockBg} flexDirection="column" paddingX={1} width={bodyColumns}>
          <Text> </Text>
          <Markdown columns={Math.max(1, bodyColumns - 2)} flushTop text={props.text} />
          <Text> </Text>
        </Box>
      </Box>
    </Box>
  )
}

export function ToolBlock(props: { colors?: ThemeColors; columns?: number; display: "quiet" | "summary" | "full"; gap?: boolean; tools: ToolResultView[] }) {
  if (props.display === "quiet") return null
  const columns = Math.max(24, props.columns ?? 96)
  const theme = props.colors ?? colors
  return (
    <Box flexDirection="row" marginBottom={1}>
      <GutterStrip color={theme.muted} />
      <Box backgroundColor={theme.assistantBlockBg} flexDirection="column" paddingX={1} width={columns}>
        {props.tools.map((tool) => (
          <Box key={`${tool.index}-${tool.id}`} flexDirection="column">
            <Text color={toolStatusColor(tool.status, theme)}>
              {toolStatusIcon(tool.status)} {tool.name}
              <Text color={theme.muted}> · {truncateEnd(tool.summary || tool.status, Math.max(16, columns - displayWidth(tool.name) - 8))}</Text>
            </Text>
            {props.display === "full" ? <FullToolDetails columns={columns - 2} tool={tool} /> : null}
          </Box>
        ))}
      </Box>
    </Box>
  )
}

function toolStatusIcon(status: ToolResultView["status"]): string {
  if (status === "running") return "…"
  return status === "ok" ? "✓" : "✕"
}

function toolStatusColor(status: ToolResultView["status"], theme: ThemeColors): string {
  if (status === "running") return theme.notice
  return status === "ok" ? theme.success : theme.error
}

export function ProgressBlock(props: { colors?: ThemeColors; columns?: number; text: string }) {
  const columns = Math.max(24, props.columns ?? 96)
  const theme = props.colors ?? colors
  return (
    <Box flexDirection="row" marginBottom={1}>
      <GutterStrip color={theme.muted} />
      <Box backgroundColor={theme.assistantBlockBg} paddingX={1} width={columns}>
        <Text color={theme.notice} wrap="wrap">
          {props.text}
        </Text>
      </Box>
    </Box>
  )
}

function GutterStrip(props: { color: string; side?: "left" | "right" }) {
  const side = props.side ?? "left"
  return (
    <Box alignSelf="stretch" backgroundColor={props.color} marginLeft={side === "right" ? 2 : 0} marginRight={side === "left" ? 2 : 0} width={1}>
      <Text> </Text>
    </Box>
  )
}

function MessageLabel(props: { align: UserMessageAlign; columns: number; color: string; label: string; side: "left" | "right"; width: number }) {
  const gutterColumns = 3
  const rowWidth = Math.min(props.columns, props.width + gutterColumns)
  const label = (
    <Box paddingLeft={1} width={props.width}>
      <Text color={props.color} bold>
        {props.label}
      </Text>
    </Box>
  )
  return (
    <Box justifyContent={props.align === "right" ? "flex-end" : "flex-start"} width={props.columns}>
      <Box flexDirection="row" width={rowWidth}>
        {props.side === "left" ? <Box width={gutterColumns} /> : null}
        {label}
        {props.side === "right" ? <Box width={gutterColumns} /> : null}
      </Box>
    </Box>
  )
}

function FullToolDetails(props: { columns: number; tool: ToolResultView }) {
  return (
    <Box flexDirection="column" marginLeft={2}>
      <Detail columns={props.columns} label="arguments" value={props.tool.arguments} />
      <Detail columns={props.columns} label="result" value={props.tool.result} />
      <Detail columns={props.columns} label="model_output" value={props.tool.model_output} />
    </Box>
  )
}

function Detail(props: { columns: number; label: string; value: unknown }) {
  if (props.value == null || props.value === "") return null
  const text = typeof props.value === "string" ? props.value : JSON.stringify(props.value, null, 2)
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={colors.muted}>{props.label}</Text>
      <Markdown columns={props.columns} text={`\`\`\`\n${text}\n\`\`\``} />
    </Box>
  )
}

function NoticeBlock(props: { colors?: ThemeColors; gap?: boolean; level?: string; text: string }) {
  const theme = props.colors ?? colors
  const color = props.level === "error" ? theme.error : theme.notice
  return (
    <Box flexDirection="row" marginBottom={1}>
      <Box width={3}>
        <Text color={color}>•</Text>
      </Box>
      <Text color={color} wrap="wrap">
        {props.text}
      </Text>
    </Box>
  )
}

function longestLineWidth(value: string): number {
  return Math.max(0, ...value.split("\n").map(displayWidth))
}

function group(item: TranscriptItem): string {
  if (item.type === "message") return item.role === "user" ? "user" : "assistant"
  if (item.type === "progress") return "progress"
  return item.type
}
