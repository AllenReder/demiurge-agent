def process(ctx):
    ctx.input.add_context(ctx.input.raw_text, role="user")
