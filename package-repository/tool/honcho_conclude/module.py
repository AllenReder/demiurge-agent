from __future__ import annotations

from demiurge.sdk import ToolResult

from .memory_honcho.config import load_config
from .memory_honcho.format import tool_display_output, tool_json_result
from .memory_honcho.runtime import tool_call


def execute(ctx, args):
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    try:
        result = tool_call(ctx, load_config(__file__), "honcho_conclude", args or {})
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    content = tool_json_result(result)
    return ToolResult(
        content=content,
        data=result,
        is_error=not bool(result.get("success")),
        model_output=content,
        display_output=tool_display_output(result, label="Honcho conclude"),
    )
