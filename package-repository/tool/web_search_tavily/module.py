from __future__ import annotations

import asyncio

from demiurge.sdk import ToolResult

from .web_search_tavily.search import load_search_config, search_web, tool_display_output, tool_json_result


async def execute(ctx, args):
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    config = load_search_config()
    result = await asyncio.to_thread(search_web, args or {}, config)
    content = tool_json_result(result)
    return ToolResult(
        content=content,
        data=result,
        is_error=not bool(result.get("success")),
        model_output=content,
        display_output=tool_display_output(result),
    )
