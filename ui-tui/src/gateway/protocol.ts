export const TUI_PROTOCOL_VERSION = 1
export const TUI_BUILD_STAMP = "demiurge-operator-v1"

export type GatewayIdentity = {
  protocol_version: number
  build_stamp: string
}

export function validateGatewayIdentity(value: unknown): GatewayIdentity {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("gateway initialize result must be an object")
  }
  const result = value as Record<string, unknown>
  if (result.protocol_version !== TUI_PROTOCOL_VERSION) {
    throw new Error(`TUI protocol mismatch: expected ${TUI_PROTOCOL_VERSION}, got ${String(result.protocol_version)}`)
  }
  if (result.build_stamp !== TUI_BUILD_STAMP) {
    throw new Error(`TUI build mismatch: expected ${TUI_BUILD_STAMP}, got ${String(result.build_stamp)}`)
  }
  return {
    protocol_version: TUI_PROTOCOL_VERSION,
    build_stamp: TUI_BUILD_STAMP,
  }
}
