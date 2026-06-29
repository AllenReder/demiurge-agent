def process(ctx):
    ctx.input.add(
        "system",
        "Summarize the user-provided assistant reply into brief spoken text. Do not add markup.",
    )
    ctx.input.add("user", ctx.input.raw_input.text, history_policy="persist")
