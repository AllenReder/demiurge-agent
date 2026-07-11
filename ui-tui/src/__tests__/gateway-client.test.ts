import { describe, expect, it, vi } from "vitest"
import { GatewayClient } from "../gateway/client"
import { TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION } from "../gateway/protocol"

describe("GatewayClient protocol identity", () => {
  it("sends the bundled identity and accepts the matching host", async () => {
    const client = new GatewayClient()
    const request = vi.spyOn(client, "request").mockResolvedValue({
      protocol_version: TUI_PROTOCOL_VERSION,
      build_stamp: TUI_BUILD_STAMP,
    })

    await client.initialize()

    expect(request).toHaveBeenCalledWith("operator.initialize", {
      protocol_version: TUI_PROTOCOL_VERSION,
      build_stamp: TUI_BUILD_STAMP,
    })
  })

  it("rejects a host identity mismatch", async () => {
    const client = new GatewayClient()
    vi.spyOn(client, "request").mockResolvedValue({
      protocol_version: TUI_PROTOCOL_VERSION,
      build_stamp: "stale-host-build",
    })

    await expect(client.initialize()).rejects.toThrow("TUI build mismatch")
  })
})
