def process(ctx):
    ctx.result.set({"text": str(ctx.output.content or "").strip()})
