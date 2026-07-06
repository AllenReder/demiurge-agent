import type { AppState } from "../types"
import type { GatewayClient } from "../gateway/client"

export async function submitComposer(client: GatewayClient, text: string): Promise<void> {
  const trimmed = text.trim()
  if (!trimmed) return
  if (trimmed.startsWith("/")) await client.request("operator.command", { text: trimmed })
  else await client.request("operator.submit", { text: trimmed })
}

export async function submitPrompt(client: GatewayClient, state: AppState): Promise<boolean> {
  const prompt = state.prompt
  if (!prompt) return false
  if (prompt.type === "approval") {
    const decision = prompt.selected === 0 ? "allow" : prompt.selected === 1 ? "session" : "deny"
    await client.request("operator.reply_approval", { approval_id: prompt.approval_id, decision })
    return true
  }
  const answer = prompt.choices[prompt.selected] ?? prompt.records?.[prompt.selected]?.session_id ?? ""
  await client.request("operator.reply_prompt", { prompt_id: prompt.prompt_id, answer })
  return true
}
