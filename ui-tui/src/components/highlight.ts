import { createHighlighterCore } from "shiki/core"
import { createJavaScriptRegexEngine } from "shiki/engine/javascript"
import githubDark from "@shikijs/themes/github-dark"
import ts from "@shikijs/langs/typescript"
import tsx from "@shikijs/langs/tsx"
import js from "@shikijs/langs/javascript"
import jsx from "@shikijs/langs/jsx"
import python from "@shikijs/langs/python"
import shell from "@shikijs/langs/shellscript"
import zsh from "@shikijs/langs/zsh"
import json from "@shikijs/langs/json"
import yaml from "@shikijs/langs/yaml"
import markdown from "@shikijs/langs/markdown"
import diff from "@shikijs/langs/diff"
import sql from "@shikijs/langs/sql"
import go from "@shikijs/langs/go"
import rust from "@shikijs/langs/rust"
import toml from "@shikijs/langs/toml"
import type { ThemedToken } from "shiki"

const loadedLanguages = [ts, tsx, js, jsx, python, shell, zsh, json, yaml, markdown, diff, sql, go, rust, toml]

const languageAliases: Record<string, string> = {
  bash: "shellscript",
  md: "markdown",
  py: "python",
  sh: "shellscript",
  shell: "shellscript",
  yml: "yaml",
}

const highlighter = createHighlighterCore({
  engine: createJavaScriptRegexEngine(),
  langs: loadedLanguages,
  themes: [githubDark],
})

export type HighlightedLine = Array<{ color?: string; content: string }>

export async function highlightCode(code: string, lang: string | undefined): Promise<HighlightedLine[]> {
  const language = normalizeLanguage(lang)
  if (!language) return plainLines(code)
  try {
    const instance = await highlighter
    const tokens = instance.codeToTokensBase(code, { lang: language, theme: "github-dark" }) as ThemedToken[][]
    return tokens.map((line) => line.map((token) => ({ color: token.color, content: token.content })))
  } catch {
    return plainLines(code)
  }
}

export function normalizeLanguage(lang: string | undefined): string | undefined {
  const value = lang?.trim().toLowerCase()
  if (!value) return undefined
  return languageAliases[value] ?? value
}

function plainLines(code: string): HighlightedLine[] {
  return code.split("\n").map((line) => [{ content: line }])
}
