import { Box, useApp, useInput, useStdout } from "ink"
import { useEffect, useMemo, useRef, useState } from "react"
import { clearPrompt, createInitialState, reduceGatewayEvent, selectPromptChoice, toggleApprovalCommand } from "./app/state"
import { submitComposer, submitPrompt } from "./app/actions"
import { Transcript } from "./components/Transcript"
import { Composer } from "./components/Composer"
import { ActivityBar, Footer } from "./components/Footer"
import { PromptPanel } from "./components/PromptPanel"
import { themedColors } from "./components/theme"
import { GatewayClient } from "./gateway/client"
import { clampColumns } from "./lib/terminal"
import type { GatewayEvent } from "./types"

export function App(props: { client: GatewayClient }) {
  const [state, setState] = useState(createInitialState)
  const pendingDeltas = useRef<GatewayEvent[]>([])
  const deltaTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const app = useApp()
  const { stdout } = useStdout()
  const columns = clampColumns(stdout.columns)

  useEffect(() => {
    const flushDeltas = () => {
      if (deltaTimer.current !== null) {
        clearTimeout(deltaTimer.current)
        deltaTimer.current = null
      }
      const events = pendingDeltas.current
      pendingDeltas.current = []
      if (!events.length) return
      setState((current) => events.reduce(reduceGatewayEvent, current))
    }
    const scheduleDeltaFlush = () => {
      if (deltaTimer.current !== null) return
      deltaTimer.current = setTimeout(flushDeltas, 50)
    }
    const unsubscribe = props.client.onEvent((event) => {
      if (event.event === "interaction.message.part.delta") {
        pendingDeltas.current.push(event)
        scheduleDeltaFlush()
        return
      }
      flushDeltas()
      setState((current) => reduceGatewayEvent(current, event))
      if (event.event === "channel.shutdown") app.exit()
    })
    props.client.start()
    void props.client.request("interaction.initialize").catch((error) => {
      setState((current) =>
        reduceGatewayEvent(current, {
          event: "interaction.error",
          payload: { message: error instanceof Error ? error.message : String(error), source: "gateway" },
        }),
      )
    })
    return () => {
      if (deltaTimer.current !== null) {
        clearTimeout(deltaTimer.current)
        deltaTimer.current = null
      }
      pendingDeltas.current = []
      unsubscribe()
      props.client.shutdown()
    }
  }, [app, props.client])

  const prompt = state.prompt
  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      if (state.status.status === "running") void props.client.request("channel.interrupt", { reason: "Ctrl-C" })
      else {
        props.client.shutdown()
        app.exit()
      }
      return
    }
    if (!prompt) return
    if (key.upArrow) {
      setState((value) => selectPromptChoice(value, -1))
      return
    }
    if (key.downArrow) {
      setState((value) => selectPromptChoice(value, 1))
      return
    }
    if (input === "f" && prompt.type === "approval") {
      setState(toggleApprovalCommand)
      return
    }
    if (key.escape) {
      if (prompt.type === "approval") {
        void props.client.request("interaction.reply_approval", { approval_id: prompt.approval_id, decision: "deny" })
      }
      setState(clearPrompt)
      return
    }
    if (key.return) {
      void submitPrompt(props.client, state).then((accepted) => {
        if (accepted) setState(clearPrompt)
      })
    }
  })

  const items = useMemo(() => state.transcript, [state.transcript])
  const theme = useMemo(() => themedColors(state.status), [state.status.demiurge_theme_color, state.status.user_theme_color])
  return (
    <Box flexDirection="column" minHeight={10} width={columns}>
      <Transcript colors={theme} columns={columns} items={items} userMessageAlign={state.status.user_message_align} />
      {prompt ? <PromptPanel colors={theme} columns={columns} prompt={prompt} /> : null}
      <ActivityBar colors={theme} columns={columns} status={state.status} />
      <Composer
        colors={theme}
        columns={columns}
        disabled={prompt !== null}
        slashCommands={state.slashCommands}
        onSubmit={(value) => {
          void submitComposer(props.client, value)
        }}
      />
      <Footer colors={theme} columns={columns} status={state.status} />
    </Box>
  )
}
