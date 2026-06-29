from __future__ import annotations

from demiurge.sdk import ToolResult

from .basic_memory.store import MemoryStore, load_memory_config, tool_display_output, tool_json_result


def execute(ctx, args):
    config = load_memory_config(__file__)
    store = MemoryStore.from_config(config)
    result = store.apply_tool_args(args or {})
    content = tool_json_result(result)
    return ToolResult(
        content=content,
        data=result,
        is_error=not bool(result.get("success")),
        model_output=content,
        display_output=tool_display_output(result),
    )

