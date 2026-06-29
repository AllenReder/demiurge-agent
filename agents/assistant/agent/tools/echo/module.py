from demiurge.sdk import ToolResult


def execute(ctx, args):
    return ToolResult(content=f"echo: {args.get('text', '')}")
