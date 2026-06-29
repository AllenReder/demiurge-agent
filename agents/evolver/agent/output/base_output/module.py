def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
