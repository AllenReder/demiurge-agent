import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "ink-testing-library"
import { Composer, shouldInsertNewline } from "../components/Composer"
import { Footer } from "../components/Footer"
import { Markdown } from "../components/Markdown"
import { PromptPanel } from "../components/PromptPanel"
import { MessageBlock, ProgressBlock, ToolBlock, Transcript } from "../components/Transcript"
import { initialStatus } from "../app/state"
import { themedColors } from "../components/theme"
import { applySlashSuggestion, exactSlashCommand, slashSuggestions, slashTokenAtCursor } from "../lib/slash"
import { displayWidth } from "../lib/terminal"

afterEach(() => cleanup())

describe("Ink TUI components", () => {
  it("renders user block and assistant markdown flow", () => {
    const user = render(<MessageBlock columns={80} role="user" text="hello" />)
    expect(user.lastFrame()).toContain("you")
    expect(user.lastFrame()).toContain("hello")
    expect(user.lastFrame()).not.toContain("┌")

    const assistant = render(<MessageBlock columns={80} role="assistant" text="# Done\n\n- item" />)
    expect(assistant.lastFrame()).toContain("demiurge")
    expect(assistant.lastFrame()).toContain("Done")
    expect(assistant.lastFrame()).toContain("item")
    expect(assistant.lastFrame()).not.toContain("┌")
  })

  it("maps runtime theme colors onto identity and status accents", () => {
    const theme = themedColors({ demiurge_theme_color: "#ff9afc", user_theme_color: "#9cc9ff" })
    expect(theme.assistant).toBe("#ff9afc")
    expect(theme.notice).toBe("#ff9afc")
    expect(theme.warning).toBe("#ff9afc")
    expect(theme.user).toBe("#9cc9ff")
    expect(theme.userGutter).toBe("#9cc9ff")
    expect(theme.userBubble).toBe("#20242e")
    expect(theme.success).toBe("#7ee787")
    expect(theme.system).toBe("#d2a8ff")
  })

  it("aligns user message blocks from host UI preference", () => {
    const left = render(<MessageBlock columns={80} role="user" text="left aligned" userMessageAlign="left" />)
    const leftLabel = left.lastFrame()!.split("\n").find((line) => line.includes("you")) ?? ""
    expect(leftLabel.indexOf("you")).toBeGreaterThanOrEqual(0)
    expect(leftLabel.indexOf("you")).toBeLessThan(8)
    left.unmount()

    const right = render(<MessageBlock columns={80} role="user" text="right aligned" userMessageAlign="right" />)
    const rightLabel = right.lastFrame()!.split("\n").find((line) => line.includes("you")) ?? ""
    expect(rightLabel.indexOf("you")).toBeGreaterThan(40)
  })

  it("keeps message labels above the bubble and leaves a bottom boundary", () => {
    const user = render(<MessageBlock columns={80} role="user" text="hello" userMessageAlign="left" />)
    const userLines = user.lastFrame()!.split("\n")
    const labelIndex = userLines.findIndex((line) => line.includes("you"))
    const textIndex = userLines.findIndex((line) => line.includes("hello"))
    expect(textIndex - labelIndex).toBe(2)
    expect(userLines[textIndex + 1]).toBe("")
    expect(userLines[userLines.length - 1]).toBe("")
    user.unmount()

    const assistant = render(<MessageBlock columns={80} role="assistant" text="hello" />)
    const assistantLines = assistant.lastFrame()!.split("\n")
    const assistantLabelIndex = assistantLines.findIndex((line) => line.includes("demiurge"))
    const assistantTextIndex = assistantLines.findIndex((line) => line.includes("hello"))
    expect(assistantTextIndex - assistantLabelIndex).toBe(2)
    expect(assistantLines[assistantTextIndex + 1]).toBe("")
    expect(assistantLines[assistantLines.length - 1]).toBe("")
  })

  it("does not add a separate blank line before message labels", () => {
    const assistant = render(<MessageBlock columns={80} gap role="assistant" text="hello" />)
    expect(assistant.lastFrame()!.split("\n")[0]).toContain("demiurge")
    assistant.unmount()

    const transcript = render(
      <Transcript
        columns={80}
        items={[
          { id: "1", type: "message", role: "user", text: "hello" },
          { id: "2", type: "message", role: "assistant", text: "hi" },
        ]}
        userMessageAlign="left"
      />,
    )
    const lines = transcript.lastFrame()!.split("\n")
    const userTextIndex = lines.findIndex((line) => line.includes("hello"))
    const assistantLabelIndex = lines.findIndex((line) => line.includes("demiurge"))
    expect(assistantLabelIndex - userTextIndex).toBe(3)
  })

  it("renders left-aligned user messages as full-width blocks", () => {
    const longText = "1".repeat(70)
    const left = render(<MessageBlock columns={80} role="user" text={longText} userMessageAlign="left" />)
    expect(left.lastFrame()).toContain(longText)
    left.unmount()

    const right = render(<MessageBlock columns={80} role="user" text={longText} userMessageAlign="right" />)
    expect(right.lastFrame()).not.toContain(longText)
  })

  it("renders markdown flow with inline code, tables, quotes, and code fences", () => {
    const markdown = render(
      <Markdown
        columns={80}
        text={[
          "## Tools",
          "Use **read_file** and `patch`.",
          "- **Explore** - use `patch`",
          "",
          "> quoted",
          "",
          "| name | use |",
          "| --- | --- |",
          "| read_file | read text |",
          "",
          "```ts",
          "const ok = true",
          "```",
        ].join("\n")}
      />,
    )
    expect(markdown.lastFrame()).toContain("Tools")
    expect(markdown.lastFrame()).toContain("read_file")
    expect(markdown.lastFrame()).toContain("Explore")
    expect(markdown.lastFrame()).toContain("patch")
    expect(markdown.lastFrame()).not.toContain("**Explore**")
    expect(markdown.lastFrame()).toContain("quoted")
    expect(markdown.lastFrame()).toContain("name")
    expect(markdown.lastFrame()).toContain("const ok")
  })

  it("keeps complex list content attached to markers and renders compact code fences", () => {
    const markdown = render(
      <Markdown
        columns={54}
        text={[
          "memory 工具用于**跨会话持久化存储信息**，让你不用每次重复告诉我同样的事情。",
          "",
          "## 基本参数",
          "",
          "- **target**: 存储位置，二选一",
          "  - `memory` - 环境/项目/工具相关的技术性笔记",
          "  - `user` - 你的个人资料、偏好、习惯",
          "",
          "1. **单次操作** (`action` 参数)",
          "- [x] 已支持 task list",
          "",
          "```json",
          "{\"target\":\"user\",\"operations\":[{\"action\":\"add\",\"content\":\"用户喜欢简洁的回答，且这一行足够长需要换行\"}]}",
          "```",
        ].join("\n")}
      />,
    )
    const frame = markdown.lastFrame() ?? ""
    const lines = frame.split("\n")
    const targetLine = lines.find((line) => line.includes("target") && line.includes("存储位置")) ?? ""
    const nestedMemoryLine = lines.find((line) => line.includes("memory") && line.includes("环境/项目")) ?? ""
    const jsonLine = lines.find((line) => line.trim() === "json") ?? ""

    expect(targetLine).toContain("•")
    expect(targetLine).not.toContain("**target**")
    expect(nestedMemoryLine).toContain("•")
    expect(jsonLine).toBeTruthy()
    expect(frame).not.toContain("─ json")
    expect(frame).toContain('"target"')
    expect(lines.some((line) => line.includes("用户喜欢简洁的回答"))).toBe(true)
    expect(lines.every((line) => displayWidth(line) <= 54)).toBe(true)
  })

  it("renders narrow markdown tables as vertical key/value blocks", () => {
    const table = render(<Markdown columns={24} text={"| very long name | description |\n| --- | --- |\n| read_file | read workspace text |"} />)
    expect(table.lastFrame()).toContain("very long name:")
    expect(table.lastFrame()).toContain("description:")
  })

  it("keeps quiet tool display silent and renders summary/full rows", () => {
    const quiet = render(<ToolBlock display="quiet" tools={[{ index: 1, name: "terminal", id: "call_1", status: "ok", summary: "done" }]} />)
    expect(quiet.lastFrame()).toBe("")

    const summary = render(<ToolBlock columns={80} display="summary" gap tools={[{ index: 1, name: "terminal", id: "call_1", status: "ok", summary: "done" }]} />)
    const summaryLines = summary.lastFrame()!.split("\n")
    const summaryIndex = summaryLines.findIndex((line) => line.includes("terminal"))
    expect(summaryIndex).toBe(0)
    expect(summaryLines[summaryIndex + 1]).toBe("")
    expect(summaryLines[summaryLines.length - 1]).toBe("")
    summary.unmount()

    const full = render(
      <ToolBlock
        display="full"
        tools={[{ index: 1, name: "terminal", id: "call_1", status: "ok", summary: "done", arguments: { command: "pwd" }, result: "/tmp" }]}
      />,
    )
    expect(full.lastFrame()).toContain("terminal")
    expect(full.lastFrame()).toContain("✓")
    expect(full.lastFrame()).toContain("arguments")
    expect(full.lastFrame()).toContain("/tmp")
  })

  it("renders progress as a compact status block", () => {
    const progress = render(<ProgressBlock columns={80} text="Working on tests" />)
    const lines = progress.lastFrame()!.split("\n")
    const progressIndex = lines.findIndex((line) => line.includes("Working on tests"))
    expect(progressIndex).toBe(0)
    expect(lines[progressIndex + 1]).toBe("")
    expect(lines[lines.length - 1]).toBe("")
  })

  it("keeps progress text plain instead of parsing markdown", () => {
    const progress = render(<ProgressBlock columns={80} text="Working on **markdown** output" />)
    expect(progress.lastFrame()).toContain("**markdown**")
  })

  it("renders prompt and approval selection hints", () => {
    const question = render(
      <PromptPanel columns={80} prompt={{ type: "prompt", prompt_id: "p1", kind: "clarify", question: "Which?", choices: ["a", "b"], selected: 1 }} />,
    )
    expect(question.lastFrame()).toContain("Question")
    expect(question.lastFrame()).toContain("› 2. b")
    expect(question.lastFrame()).toContain("Enter selects")

    const approval = render(
      <PromptPanel
        columns={80}
        prompt={{
          type: "approval",
          approval_id: "a1",
          selected: 2,
          showFull: false,
          request: { tool_name: "terminal", tool_call_id: "c1", turn_id: "t1", capability: "terminal.exec", action: "exec", risk: "high", summary: "Run command" },
        }}
      />,
    )
    expect(approval.lastFrame()).toContain("Approval required")
    expect(approval.lastFrame()).toContain("› 3. deny")
    expect(approval.lastFrame()).toContain("f toggles command")
  })

  it("supports multiline composer newline key decisions", () => {
    expect(shouldInsertNewline("", { return: true, ctrl: true })).toBe(true)
    expect(shouldInsertNewline("", { return: true, meta: true })).toBe(true)
    expect(shouldInsertNewline("", { return: true })).toBe(false)
    expect(shouldInsertNewline("\n", { return: true }, { GHOSTTY_RESOURCES_DIR: "/ghostty" })).toBe(true)

    const composer = render(<Composer columns={80} disabled={false} onSubmit={() => undefined} />)
    expect(composer.lastFrame()).toContain("message")
    expect(composer.lastFrame()).toContain("Message demiurge, or type /help")
    expect(composer.lastFrame()).toContain("Enter submit · Ctrl-C interrupt")
    expect(composer.lastFrame()).not.toContain("Ctrl/Option/Shift+Enter newline")
  })

  it("filters and applies slash command suggestions", () => {
    const commands = [
      { name: "status", description: "Show runtime status", group: "Core", usage: null },
      { name: "skill", description: "View a skill", group: "Tools", usage: "/skill <name> [file_path]" },
      { name: "tool-display", description: "Show or change tool display", group: "Tools", usage: "/tool-display quiet|summary|full" },
      { name: "exit", description: "Quit", group: "Control", usage: null },
    ]

    expect(slashTokenAtCursor("/sta", 4)).toBe("/sta")
    expect(slashTokenAtCursor("/status now", 8)).toBeNull()
    expect(slashSuggestions(commands, "/sta").map((item) => item.name)).toEqual(["status"])
    expect(slashSuggestions(commands, "/display").map((item) => item.name)).toEqual(["tool-display"])
    expect(exactSlashCommand("/status", slashSuggestions(commands, "/status")[0])).toBe(true)
    expect(applySlashSuggestion("/ski", commands[1])).toEqual({ value: "/skill ", cursor: 7 })
    expect(applySlashSuggestion("/ex", commands[3])).toEqual({ value: "/exit", cursor: 5 })
  })

  it("renders footer status counters", () => {
    const footer = render(
      <Footer
        columns={100}
        status={{
          ...initialStatus,
          workspace: "/tmp/work",
          core_id: "assistant",
          core_version: "0001",
          session_id: "session_abcdef",
          provider: "fake",
          model: "fake-model",
          status: "running",
          queued_inputs: 2,
          background_tasks: 1,
          message_count: 5,
        }}
      />,
    )
    expect(footer.lastFrame()).toContain("assistant@0001")
    expect(footer.lastFrame()).toContain("fake:fake-model")
    expect(footer.lastFrame()).toContain("queued 2")
  })
})
