import { Box, Text, useInput } from "ink"
import { useEffect, useMemo, useState } from "react"
import type { SlashCommandSpec } from "../types"
import { applySlashSuggestion, exactSlashCommand, slashSuggestions, slashTokenAtCursor } from "../lib/slash"
import { lineNavigationOffset, nextOffset, previousOffset } from "../lib/terminal"
import { colors, type ThemeColors } from "./theme"

type Key = {
  backspace?: boolean
  ctrl?: boolean
  delete?: boolean
  downArrow?: boolean
  escape?: boolean
  home?: boolean
  leftArrow?: boolean
  meta?: boolean
  return?: boolean
  rightArrow?: boolean
  shift?: boolean
  upArrow?: boolean
  tab?: boolean
}

export function Composer(props: { colors?: ThemeColors; columns: number; disabled: boolean; onSubmit: (value: string) => void; slashCommands?: SlashCommandSpec[] }) {
  const theme = props.colors ?? colors
  const [draft, setDraft] = useState("")
  const [cursor, setCursor] = useState(0)
  const [slashSelected, setSlashSelected] = useState(0)
  const [slashDismissedFor, setSlashDismissedFor] = useState("")
  const inputColumns = Math.max(12, props.columns - 4)
  const slashToken = slashTokenAtCursor(draft, cursor)
  const slashItems = useMemo(() => slashSuggestions(props.slashCommands ?? [], slashToken), [props.slashCommands, slashToken])
  const slashOpen = !props.disabled && slashToken !== null && slashDismissedFor !== draft && slashItems.length > 0
  const selectedSlash = slashItems[Math.min(slashSelected, Math.max(0, slashItems.length - 1))]

  useEffect(() => {
    setSlashSelected(0)
  }, [slashToken])

  useEffect(() => {
    if (slashSelected >= slashItems.length) setSlashSelected(Math.max(0, slashItems.length - 1))
  }, [slashItems.length, slashSelected])

  useInput((input, key) => {
    if (props.disabled) return
    if (slashOpen) {
      if (key.escape) {
        setSlashDismissedFor(draft)
        return
      }
      if (key.upArrow || key.downArrow) {
        setSlashSelected((value) => (value + (key.upArrow ? -1 : 1) + slashItems.length) % slashItems.length)
        return
      }
      if ((key.tab || input === "\t") && selectedSlash) {
        const next = applySlashSuggestion(draft, selectedSlash)
        setDraft(next.value)
        setCursor(next.cursor)
        return
      }
    }
    if (key.return) {
      if (slashOpen && selectedSlash && !exactSlashCommand(slashToken, selectedSlash)) {
        const next = applySlashSuggestion(draft, selectedSlash)
        setDraft(next.value)
        setCursor(next.cursor)
        return
      }
      if (shouldInsertNewline(input, key)) {
        const next = insertAt(draft, cursor, "\n")
        setDraft(next)
        setCursor(cursor + 1)
        return
      }
      const value = draft.trim()
      if (!value) return
      props.onSubmit(value)
      setDraft("")
      setCursor(0)
      return
    }
    if (key.leftArrow) {
      setCursor((value) => previousOffset(draft, value))
      return
    }
    if (key.rightArrow) {
      setCursor((value) => nextOffset(draft, value))
      return
    }
    if (key.upArrow || key.downArrow) {
      const next = lineNavigationOffset(draft, cursor, inputColumns, key.upArrow ? -1 : 1)
      if (next !== null) setCursor(next)
      return
    }
    if (key.home) {
      setCursor(0)
      return
    }
    if (key.delete) {
      if (cursor >= draft.length) return
      const end = nextOffset(draft, cursor)
      setDraft(draft.slice(0, cursor) + draft.slice(end))
      return
    }
    if (key.backspace) {
      if (cursor <= 0) return
      const start = previousOffset(draft, cursor)
      setDraft(draft.slice(0, start) + draft.slice(cursor))
      setCursor(start)
      return
    }
    if (key.ctrl && input === "u") {
      setDraft(draft.slice(cursor))
      setCursor(0)
      return
    }
    if (key.ctrl && input === "k") {
      setDraft(draft.slice(0, cursor))
      return
    }
    const normalized = input.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
    if (!normalized || isControlInput(normalized)) return
    const next = insertAt(draft, cursor, normalized)
    setDraft(next)
    setCursor(cursor + normalized.length)
  })

  return (
    <Box flexDirection="column" marginTop={1}>
      {slashOpen ? <SlashPanel columns={props.columns} items={slashItems} selected={slashSelected} /> : null}
      <Text color={colors.border}>{"─".repeat(Math.max(1, props.columns - 1))}</Text>
      <Box flexDirection="row">
        <Box width={9}>
          <Text bold color={props.disabled ? colors.muted : theme.user}>
            {props.disabled ? "reply" : "message"}
          </Text>
        </Box>
        <Box flexDirection="column" width={inputColumns}>
          {props.disabled ? (
            <Text color={colors.muted}>Respond to the prompt above</Text>
          ) : !draft ? (
            <Text wrap="wrap">
              <Text inverse> </Text>
              <Text color={colors.placeholder} dimColor>
                Message demiurge, or type /help
              </Text>
            </Text>
          ) : (
            <Text color={colors.text} wrap="wrap">
              {renderDraft(draft, cursor)}
            </Text>
          )}
        </Box>
      </Box>
      <Box justifyContent="space-between">
        <Text color={colors.muted}>Enter submit · Ctrl-C interrupt</Text>
        <Text color={colors.muted}>{draft.length}</Text>
      </Box>
    </Box>
  )
}

export function shouldInsertNewline(input: string, key: Key, env: NodeJS.ProcessEnv = process.env): boolean {
  return Boolean(key.shift || key.ctrl || key.meta || (input === "\n" && shouldPreserveCtrlJNewline(env)))
}

export function shouldPreserveCtrlJNewline(env: NodeJS.ProcessEnv = process.env): boolean {
  return Boolean(env.GHOSTTY_RESOURCES_DIR || env.TERM_PROGRAM?.toLowerCase().includes("ghostty"))
}

function insertAt(value: string, cursor: number, insert: string): string {
  return value.slice(0, cursor) + insert + value.slice(cursor)
}

function isControlInput(value: string): boolean {
  return value.length === 1 && value.charCodeAt(0) < 32 && value !== "\n"
}

function renderDraft(value: string, cursor: number) {
  const before = value.slice(0, cursor)
  const at = value[cursor] ?? " "
  const after = value.slice(cursor + (value[cursor] ? 1 : 0))
  return (
    <>
      {before}
      <Text inverse>{at}</Text>
      {after}
    </>
  )
}

function SlashPanel(props: { columns: number; items: SlashCommandSpec[]; selected: number }) {
  const width = Math.max(24, Math.min(props.columns - 2, 92))
  return (
    <Box flexDirection="column" marginBottom={1} paddingX={1} width={width}>
      {props.items.map((item, index) => {
        const selected = index === props.selected
        return (
          <Box key={item.name} backgroundColor={selected ? colors.slashSelectedBg : colors.slashPanelBg} paddingX={1}>
            <Text color={selected ? colors.selected : colors.text}>
              {selected ? "› " : "  "}/{item.name}
              <Text color={colors.muted}> {slashDetail(item)}</Text>
              <Text color={colors.placeholder}> · {item.group}</Text>
            </Text>
          </Box>
        )
      })}
    </Box>
  )
}

function slashDetail(item: SlashCommandSpec): string {
  const usage = item.usage || ""
  const prefix = `/${item.name}`
  if (usage.startsWith(`${prefix} `)) return usage.slice(prefix.length + 1)
  return item.description
}
