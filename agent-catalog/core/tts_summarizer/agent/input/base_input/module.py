def process(ctx):
    ctx.input.add("user", ctx.input.raw_input.text, history_policy="persist")
