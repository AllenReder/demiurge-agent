import type { GatewayEvent, RpcFrame, RpcResponse } from "../types"

export function encodeRequest(id: number, method: string, params: Record<string, unknown> = {}): string {
  return JSON.stringify({ id, method, params }) + "\n"
}

export function parseRpcLine(line: string): RpcFrame | undefined {
  const trimmed = line.trim()
  if (!trimmed) return undefined
  const parsed = JSON.parse(trimmed) as unknown
  if (!parsed || typeof parsed !== "object") throw new Error("RPC frame must be an object")
  const frame = parsed as Record<string, unknown>
  if (typeof frame.event === "string") {
    return { event: frame.event, payload: recordValue(frame.payload) ?? {} } satisfies GatewayEvent
  }
  if (typeof frame.id === "number" || typeof frame.id === "string") {
    return {
      id: frame.id,
      result: frame.result,
      error: recordValue(frame.error) as RpcResponse["error"],
    } satisfies RpcResponse
  }
  throw new Error("RPC frame must contain event or id")
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
}
