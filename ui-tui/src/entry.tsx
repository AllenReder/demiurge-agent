import { render } from "ink"
import { App } from "./App"
import { GatewayClient } from "./gateway/client"

async function main() {
  if (!process.stdin.isTTY) {
    console.error("demiurge TS TUI requires an interactive terminal with TTY stdin.")
    process.exitCode = 1
    return
  }
  const client = new GatewayClient()
  try {
    render(<App client={client} />, { exitOnCtrlC: false })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error(["demiurge TS TUI failed to start.", message, "", "Build frontend assets with `cd ui-tui && npm ci && npm run build`."].join("\n"))
    process.exitCode = 1
  }
}

void main()
