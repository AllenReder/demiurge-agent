import { describe, expect, it } from "vitest"
import { encodeRequest, parseRpcLine } from "../gateway/rpc"

describe("JSON-RPC framing", () => {
  it("encodes requests as newline-delimited JSON", () => {
    expect(encodeRequest(7, "interaction.submit", { text: "hello" })).toBe(
      '{"id":7,"method":"interaction.submit","params":{"text":"hello"}}\n',
    )
  })

  it("parses event frames", () => {
    expect(parseRpcLine('{"event":"interaction.status","payload":{"status":"idle"}}')).toEqual({
      event: "interaction.status",
      payload: { status: "idle" },
    })
  })

  it("parses response frames", () => {
    expect(parseRpcLine('{"id":1,"result":{"ok":true}}')).toEqual({ id: 1, result: { ok: true } })
  })
})
