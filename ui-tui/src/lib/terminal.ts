import stringWidth from "string-width"
import wrapAnsi from "wrap-ansi"

export function displayWidth(value: string): number {
  return stringWidth(value)
}

export function clampColumns(value: number | undefined, fallback = 100): number {
  return Math.max(24, Math.floor(value || fallback))
}

export function truncateMiddle(value: string, width: number): string {
  if (width <= 0) return ""
  if (displayWidth(value) <= width) return value
  if (width <= 1) return "…"
  const target = Math.max(1, width - 1)
  const chars = [...value]
  let left = ""
  let right = ""
  let i = 0
  let j = chars.length - 1
  while (i <= j && displayWidth(left + right) < target) {
    if (displayWidth(left) <= displayWidth(right)) left += chars[i++] ?? ""
    else right = (chars[j--] ?? "") + right
  }
  while (displayWidth(left + right) > target) {
    if (displayWidth(right) >= displayWidth(left)) right = [...right].slice(1).join("")
    else left = [...left].slice(0, -1).join("")
  }
  return `${left}…${right}`
}

export function truncateEnd(value: string, width: number): string {
  if (width <= 0) return ""
  if (displayWidth(value) <= width) return value
  if (width <= 1) return "…"
  let out = ""
  for (const ch of [...value]) {
    if (displayWidth(out + ch) > width - 1) break
    out += ch
  }
  return `${out}…`
}

export function padEndColumns(value: string, width: number): string {
  return value + " ".repeat(Math.max(0, width - displayWidth(value)))
}

export function wrapText(value: string, width: number): string[] {
  const columns = Math.max(1, width)
  const lines = wrapAnsi(value || " ", columns, { hard: true, trim: false }).split("\n")
  return lines.length ? lines : [""]
}

export type CursorPosition = {
  column: number
  line: number
}

type VisualLine = {
  end: number
  start: number
}

function visualLines(value: string, cols: number): VisualLine[] {
  if (!value) return [{ start: 0, end: 0 }]
  const wrapped = wrapAnsi(value, Math.max(1, cols), { hard: true, trim: false })
  const lines: VisualLine[] = []
  let original = 0
  let start = 0
  for (const ch of wrapped) {
    if (ch === "\n") {
      lines.push({ start, end: original })
      if (value[original] === "\n") original += 1
      start = original
      continue
    }
    if (value[original] !== ch) {
      const next = value.indexOf(ch, original)
      if (next >= 0) original = next
    }
    original += ch.length
  }
  lines.push({ start, end: original })
  return lines
}

export function cursorLayout(value: string, cursor: number, columns: number): CursorPosition {
  const pos = Math.max(0, Math.min(cursor, value.length))
  const lines = visualLines(value, columns)
  let line = 0
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i]!.start <= pos) line = i
    else break
  }
  const current = lines[line]!
  return { line, column: displayWidth(value.slice(current.start, Math.min(pos, current.end))) }
}

export function inputVisualHeight(value: string, columns: number): number {
  return cursorLayout(value, value.length, columns).line + 1
}

export function offsetFromPosition(value: string, row: number, col: number, columns: number): number {
  const lines = visualLines(value, columns)
  const target = lines[Math.max(0, Math.min(lines.length - 1, Math.floor(row)))]!
  let width = 0
  for (let index = target.start; index < target.end; index += 1) {
    const nextWidth = width + displayWidth(value[index] ?? "")
    if (col < nextWidth) return index
    width = nextWidth
  }
  return target.end
}

export function previousOffset(value: string, cursor: number): number {
  if (cursor <= 0) return 0
  return Math.max(0, cursor - [...value.slice(0, cursor)].at(-1)!.length)
}

export function nextOffset(value: string, cursor: number): number {
  if (cursor >= value.length) return value.length
  const next = [...value.slice(cursor)][0]
  return Math.min(value.length, cursor + (next?.length ?? 1))
}

export function lineNavigationOffset(value: string, cursor: number, columns: number, delta: -1 | 1): number | null {
  const current = cursorLayout(value, cursor, columns)
  const nextLine = current.line + delta
  if (nextLine < 0) return null
  const lines = visualLines(value, columns)
  if (nextLine >= lines.length) return null
  return offsetFromPosition(value, nextLine, current.column, columns)
}
