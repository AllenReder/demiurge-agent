import { Box, Text } from "ink"
import { Lexer, type Token, type Tokens } from "marked"
import { Fragment, type ReactNode, useEffect, useMemo, useState } from "react"
import { displayWidth, padEndColumns, truncateEnd } from "../lib/terminal"
import { highlightCode, normalizeLanguage, type HighlightedLine } from "./highlight"
import { colors } from "./theme"

type MarkdownProps = {
  columns: number
  flushTop?: boolean
  text: string
}

export function Markdown(props: MarkdownProps) {
  const tokens = useMemo(() => Lexer.lex(props.text, { gfm: true }), [props.text])
  return (
    <Box flexDirection="column" width={props.columns}>
      {tokens.map((token, index) => (
        <Block key={index} columns={props.columns} marginTop={!(props.flushTop && index === 0)} token={token} />
      ))}
    </Box>
  )
}

function Block(props: { columns: number; marginTop?: boolean; token: Token }) {
  const token = props.token
  const marginTop = props.marginTop === false ? 0 : 1
  if (token.type === "space" || token.type === "def") return null
  if (token.type === "heading") {
    return (
      <Box marginTop={marginTop} width={props.columns}>
        <Text bold color={colors.text}>
          {token.depth <= 2 ? "▌ " : "· "}
          <Inline tokens={token.tokens} />
        </Text>
      </Box>
    )
  }
  if (token.type === "paragraph") {
    return (
      <Box marginTop={marginTop} width={props.columns}>
        <Text color={colors.text} wrap="wrap">
          <Inline tokens={token.tokens} />
        </Text>
      </Box>
    )
  }
  if (token.type === "code") return <CodeBlock code={token.text} columns={props.columns} lang={token.lang} marginTop={marginTop} />
  if (token.type === "blockquote") {
    return (
      <Box flexDirection="row" marginTop={marginTop}>
        <Text color={colors.border}>│ </Text>
        <Box flexDirection="column" width={Math.max(1, props.columns - 2)}>
          {(token.tokens ?? []).map((child, index) => (
            <Block key={index} columns={Math.max(1, props.columns - 2)} marginTop={index > 0} token={child} />
          ))}
        </Box>
      </Box>
    )
  }
  if (token.type === "list") return <ListBlock columns={props.columns} marginTop={marginTop} token={token as Tokens.List} />
  if (token.type === "table") return <TableBlock columns={props.columns} marginTop={marginTop} token={token as Tokens.Table} />
  if (token.type === "hr") {
    return (
      <Box marginTop={marginTop}>
        <Text color={colors.border}>{thinRule(Math.min(props.columns, 72))}</Text>
      </Box>
    )
  }
  if (token.type === "html") {
    return (
      <Box marginTop={marginTop}>
        <Text color={colors.muted}>{token.text}</Text>
      </Box>
    )
  }
  return (
    <Box marginTop={marginTop} width={props.columns}>
      <Text color={colors.text}>{plainText(token)}</Text>
    </Box>
  )
}

function Inline(props: { tokens: Token[] | undefined }): ReactNode {
  if (!props.tokens?.length) return null
  return props.tokens.map((token, index) => <Fragment key={index}>{inlineToken(token)}</Fragment>)
}

function inlineToken(token: Token): ReactNode {
  if (token.type === "text" || token.type === "escape") {
    const nested = "tokens" in token ? token.tokens : undefined
    return nested?.length ? <Inline tokens={nested} /> : token.text
  }
  if (token.type === "strong") {
    return (
      <Text bold color={colors.textStrong}>
        <Inline tokens={token.tokens} />
      </Text>
    )
  }
  if (token.type === "em") {
    return (
      <Text italic>
        <Inline tokens={token.tokens} />
      </Text>
    )
  }
  if (token.type === "del") {
    return (
      <Text strikethrough>
        <Inline tokens={token.tokens} />
      </Text>
    )
  }
  if (token.type === "codespan") {
    return (
      <Text color={colors.code} backgroundColor={colors.codeBg}>
        {` ${token.text} `}
      </Text>
    )
  }
  if (token.type === "link") {
    return (
      <Text color={colors.link} underline>
        <Inline tokens={token.tokens} />
      </Text>
    )
  }
  if (token.type === "image") {
    return <Text color={colors.muted}>[image: {token.text || token.href}]</Text>
  }
  if (token.type === "br") return "\n"
  if (token.type === "html") return <Text color={colors.muted}>{token.text}</Text>
  return plainText(token)
}

function ListBlock(props: { columns: number; marginTop?: number; token: Tokens.List }) {
  const start = typeof props.token.start === "number" ? props.token.start : 1
  return (
    <Box flexDirection="column" marginTop={props.marginTop ?? 1}>
      {props.token.items.map((item, index) => {
        const marker = props.token.ordered ? `${start + index}.` : item.task ? (item.checked ? "☑" : "☐") : "•"
        const { inlineTokens, remaining } = splitListItem(item.tokens)
        return (
          <Box key={index} flexDirection="row">
            <Box width={4}>
              <Text color={colors.muted}>{marker}</Text>
            </Box>
            <Box flexDirection="column" width={Math.max(1, props.columns - 4)}>
              {inlineTokens ? (
                <Text color={colors.text} wrap="wrap">
                  <Inline tokens={inlineTokens} />
                </Text>
              ) : null}
              {remaining.map((child, childIndex) => (
                <Block key={childIndex} columns={Math.max(1, props.columns - 4)} marginTop={childIndex > 0} token={child} />
              ))}
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}

function splitListItem(tokens: Token[]): { inlineTokens: Token[] | undefined; remaining: Token[] } {
  let firstContentIndex = 0
  while (tokens[firstContentIndex]?.type === "checkbox") firstContentIndex += 1
  const firstToken = tokens[firstContentIndex]
  if (!firstToken) return { inlineTokens: undefined, remaining: [] }
  if ((firstToken.type === "text" || firstToken.type === "paragraph") && "tokens" in firstToken && Array.isArray(firstToken.tokens)) {
    return { inlineTokens: firstToken.tokens, remaining: tokens.slice(firstContentIndex + 1) }
  }
  return { inlineTokens: undefined, remaining: tokens.slice(firstContentIndex) }
}

function TableBlock(props: { columns: number; marginTop?: number; token: Tokens.Table }) {
  const headers = props.token.header.map((cell) => cellText(cell))
  const rows = props.token.rows.map((row) => row.map((cell) => cellText(cell)))
  const table = [headers, ...rows]
  if (!headers.length) return null
  const widths = headers.map((_, col) => Math.max(3, ...table.map((row) => displayWidth(row[col] ?? ""))))
  const total = widths.reduce((sum, width) => sum + width, 0) + Math.max(0, widths.length - 1) * 3
  const tooNarrow = total > props.columns || widths.length > 5
  if (tooNarrow) return <VerticalTable columns={props.columns} headers={headers} marginTop={props.marginTop} rows={rows} />
  const separator = widths.map((width) => "─".repeat(width)).join("  ")
  return (
    <Box flexDirection="column" marginTop={props.marginTop ?? 1} paddingLeft={2}>
      <Text bold color={colors.text}>
        {formatTableRow(headers, widths)}
      </Text>
      <Text color={colors.border}>{separator}</Text>
      {rows.map((row, index) => (
        <Text key={index} color={colors.text}>
          {formatTableRow(row, widths)}
        </Text>
      ))}
    </Box>
  )
}

function VerticalTable(props: { columns: number; headers: string[]; marginTop?: number; rows: string[][] }) {
  return (
    <Box flexDirection="column" marginTop={props.marginTop ?? 1} paddingLeft={2}>
      {props.rows.map((row, rowIndex) => (
        <Box key={rowIndex} flexDirection="column" marginTop={rowIndex ? 1 : 0}>
          {rowIndex ? <Text color={colors.border}>{thinRule(Math.min(props.columns - 2, 40))}</Text> : null}
          {props.headers.map((header, colIndex) => (
            <Text key={colIndex} color={colors.text} wrap="wrap">
              <Text bold color={colors.textStrong}>
                {header || `Column ${colIndex + 1}`}:
              </Text>{" "}
              {row[colIndex] ?? ""}
            </Text>
          ))}
        </Box>
      ))}
    </Box>
  )
}

function CodeBlock(props: { code: string; columns: number; lang?: string; marginTop?: number }) {
  const [lines, setLines] = useState<HighlightedLine[] | null>(null)
  const lang = normalizeLanguage(props.lang)
  useEffect(() => {
    let cancelled = false
    void highlightCode(props.code, props.lang).then((next) => {
      if (!cancelled) setLines(next)
    })
    return () => {
      cancelled = true
    }
  }, [props.code, props.lang])

  const visibleLines: HighlightedLine[] = lines ?? props.code.split("\n").map((line) => [{ content: line }])
  const innerColumns = Math.max(1, props.columns - 2)
  const textColumns = Math.max(1, innerColumns - 2)
  const wrappedLines = visibleLines.flatMap((line) => wrapHighlightedLine(line, textColumns))
  return (
    <Box flexDirection="column" marginTop={props.marginTop ?? 1} paddingLeft={2} width={props.columns}>
      {lang ? (
        <Box backgroundColor={colors.codeBg} paddingX={1} width={innerColumns}>
          <Text bold color={colors.muted}>
            {lang}
          </Text>
        </Box>
      ) : null}
      <Box backgroundColor={colors.codeBg} flexDirection="column" paddingX={1} width={innerColumns}>
        {wrappedLines.map((line, index) => (
          <Text key={index} color={colors.text}>
            {line.length
              ? line.map((token, tokenIndex) => (
                  <Text key={tokenIndex} color={token.color}>
                    {token.content}
                  </Text>
                ))
              : " "}
          </Text>
        ))}
      </Box>
    </Box>
  )
}

function wrapHighlightedLine(line: HighlightedLine, columns: number): HighlightedLine[] {
  const width = Math.max(1, columns)
  if (!line.length) return [[]]
  const wrapped: HighlightedLine[] = []
  let current: HighlightedLine = []
  let currentWidth = 0

  function pushCurrent() {
    wrapped.push(current)
    current = []
    currentWidth = 0
  }

  for (const token of line) {
    let chunk = ""
    for (const char of [...token.content]) {
      const charWidth = displayWidth(char)
      if (currentWidth > 0 && currentWidth + charWidth > width) {
        if (chunk) {
          current.push({ ...token, content: chunk })
          chunk = ""
        }
        pushCurrent()
      }
      chunk += char
      currentWidth += charWidth
    }
    if (chunk) current.push({ ...token, content: chunk })
  }

  if (current.length || !wrapped.length) pushCurrent()
  return wrapped
}

function cellText(cell: Tokens.TableCell): string {
  return cell.tokens.map(plainText).join("").replace(/\s+/g, " ").trim()
}

function formatTableRow(row: string[], widths: number[]): string {
  return widths.map((width, index) => padEndColumns(truncateEnd(row[index] ?? "", width), width)).join("  ")
}

function plainText(token: Token): string {
  if ("text" in token && typeof token.text === "string") return token.text
  if ("tokens" in token && Array.isArray(token.tokens)) return token.tokens.map(plainText).join("")
  if ("raw" in token && typeof token.raw === "string") return token.raw
  return ""
}

function thinRule(width: number): string {
  return "─".repeat(Math.max(1, width))
}
