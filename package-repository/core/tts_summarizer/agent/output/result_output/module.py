def process(ctx):
    ctx.result.set({"text": str(ctx.output.response_text or "").strip()})
