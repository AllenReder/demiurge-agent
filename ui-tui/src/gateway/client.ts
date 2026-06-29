import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { createInterface } from "node:readline"
import { encodeRequest, parseRpcLine } from "./rpc"
import type { GatewayEvent, RpcResponse } from "../types"

type Pending = {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
}

export class GatewayClient {
  private child: ChildProcessWithoutNullStreams | undefined
  private nextId = 1
  private pending = new Map<number, Pending>()
  private handlers = new Set<(event: GatewayEvent) => void>()

  onEvent(handler: (event: GatewayEvent) => void): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  start(): void {
    if (this.child) return
    const python = process.env.DEMIURGE_TUI_GATEWAY_PYTHON || process.env.PYTHON || "python3"
    this.child = spawn(python, ["-m", "demiurge.ui_gateway.entry"], {
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"],
    })
    this.child.stdin.on("error", () => undefined)
    const stdout = createInterface({ input: this.child.stdout })
    stdout.on("line", (line) => this.handleLine(line))
    const stderr = createInterface({ input: this.child.stderr })
    stderr.on("line", (line) => this.dispatch({ event: "interaction.error", payload: { source: "gateway.stderr", message: line } }))
    this.child.on("exit", (code, signal) => {
      this.child = undefined
      this.rejectPending(new Error(`gateway exited (${code ?? signal ?? "unknown"})`))
      if (code === 0 && signal == null) {
        this.dispatch({ event: "channel.shutdown", payload: {} })
      } else {
        this.dispatch({
          event: "interaction.error",
          payload: { source: "gateway", message: `gateway exited (${code ?? signal ?? "unknown"})` },
        })
      }
    })
  }

  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.child) this.start()
    const child = this.child
    if (!child) return Promise.reject(new Error("gateway child was not started"))
    const id = this.nextId++
    child.stdin.write(encodeRequest(id, method, params))
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
    })
  }

  shutdown(): void {
    const child = this.child
    if (!child) return
    if (!child.stdin.destroyed && child.stdin.writable) void this.request("channel.shutdown").catch(() => undefined)
    child.stdin.end()
  }

  private handleLine(line: string): void {
    let frame: GatewayEvent | RpcResponse | undefined
    try {
      frame = parseRpcLine(line)
    } catch (error) {
      this.dispatch({
        event: "interaction.error",
        payload: { source: "gateway.protocol", message: error instanceof Error ? error.message : String(error) },
      })
      return
    }
    if (!frame) return
    if ("event" in frame) {
      this.dispatch(frame)
      return
    }
    const id = typeof frame.id === "number" ? frame.id : Number(frame.id)
    const pending = this.pending.get(id)
    if (!pending) return
    this.pending.delete(id)
    if (frame.error) pending.reject(new Error(frame.error.message))
    else pending.resolve(frame.result)
  }

  private dispatch(event: GatewayEvent): void {
    for (const handler of this.handlers) handler(event)
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) pending.reject(error)
    this.pending.clear()
  }
}
