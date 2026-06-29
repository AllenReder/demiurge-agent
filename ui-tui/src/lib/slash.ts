import type { SlashCommandSpec } from "../types"

export type SlashSuggestion = SlashCommandSpec & {
  display: string
}

export function slashTokenAtCursor(value: string, cursor: number): string | null {
  if (!value.startsWith("/")) return null
  const firstLineEnd = value.indexOf("\n")
  const commandLineEnd = firstLineEnd === -1 ? value.length : firstLineEnd
  if (cursor > commandLineEnd) return null
  const space = value.search(/\s/)
  const tokenEnd = space === -1 ? value.length : space
  if (cursor > tokenEnd) return null
  const token = value.slice(0, tokenEnd)
  if (!/^\/[^\s/]*$/.test(token)) return null
  return token
}

export function slashSuggestions(commands: SlashCommandSpec[], token: string | null, limit = 8): SlashSuggestion[] {
  if (token === null) return []
  const query = token.slice(1).toLowerCase()
  const scored = commands
    .map((command, index) => {
      const name = command.name.toLowerCase()
      const usage = (command.usage ?? `/${command.name}`).toLowerCase()
      const description = command.description.toLowerCase()
      const score =
        query === ""
          ? 0
          : name === query
            ? 1
            : name.startsWith(query)
              ? 2
              : name.includes(query)
                ? 3
                : usage.includes(query) || description.includes(query)
                  ? 4
                  : 0
      return score ? { command, index, score } : query === "" ? { command, index, score: 5 } : null
    })
    .filter((item): item is { command: SlashCommandSpec; index: number; score: number } => Boolean(item))
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, limit)
  return scored.map(({ command }) => ({ ...command, display: command.usage || `/${command.name}` }))
}

export function applySlashSuggestion(value: string, suggestion: SlashCommandSpec): { cursor: number; value: string } {
  const space = value.search(/\s/)
  const tokenEnd = space === -1 ? value.length : space
  const command = `/${suggestion.name}${needsArgumentSpace(suggestion) ? " " : ""}`
  const next = command + value.slice(tokenEnd)
  return { value: next, cursor: command.length }
}

export function exactSlashCommand(token: string | null, suggestion: SlashCommandSpec | undefined): boolean {
  return Boolean(token && suggestion && token.slice(1).toLowerCase() === suggestion.name.toLowerCase())
}

function needsArgumentSpace(suggestion: SlashCommandSpec): boolean {
  const usage = suggestion.usage || ""
  return usage.startsWith(`/${suggestion.name} `)
}
