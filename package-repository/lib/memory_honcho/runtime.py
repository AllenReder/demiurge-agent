from __future__ import annotations

from typing import Any, Mapping

from .client import HonchoAdapter, HonchoUnavailable
from .format import render_context_block, render_static_guidance, sanitize_turn_text
from .session import session_ref_from_ctx
from .store import HonchoStore, session_ref_from_record


def bootstrap_context(ctx: Any, config: Mapping[str, Any]) -> list[str]:
    ref = session_ref_from_ctx(ctx, config)
    store = HonchoStore.from_config(config)
    fragments = [render_static_guidance(config)]
    cached = store.read_cache(ref)
    block = render_context_block(cached, source="cached Honcho context")
    if block:
        fragments.append(block)
    return fragments


def recall(query: str, ctx: Any, config: Mapping[str, Any]) -> str:
    if str(config.get("recall_mode") or "hybrid") == "tools":
        return ""
    ref = session_ref_from_ctx(ctx, config)
    store = HonchoStore.from_config(config)
    cached = store.read_cache(ref)
    try:
        context = HonchoAdapter(config).fetch_context(ref, query=query)
        if context:
            store.write_cache(ref, context)
            cached = context
    except HonchoUnavailable:
        pass
    except Exception:
        pass
    return render_context_block(cached, source="Honcho context")


def sync_turn(ctx: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    ref = session_ref_from_ctx(ctx, config)
    store = HonchoStore.from_config(config)
    turn_id = str(getattr(getattr(ctx, "turn", None), "turn_id", "") or "")
    user_text = sanitize_turn_text(str(getattr(getattr(getattr(ctx, "turn", None), "user_input", None), "content", "") or ""))
    assistant_text = sanitize_turn_text(str(getattr(getattr(ctx, "output", None), "content", "") or ""))
    enqueued = store.enqueue_turn(ref, turn_id=turn_id, user_text=user_text, assistant_text=assistant_text)
    synced = drain_outbox(config)
    prefetched = prefetch_next(user_text, ref, config)
    return {"success": True, "enqueued": enqueued, "synced": synced, "prefetched": prefetched}


def drain_outbox(config: Mapping[str, Any]) -> int:
    store = HonchoStore.from_config(config)
    try:
        adapter = HonchoAdapter(config)
    except Exception:
        return 0
    count = 0
    for record in store.pending_turns():
        ref = session_ref_from_record(record)
        if ref is None:
            continue
        try:
            adapter.sync_turn(
                ref,
                sanitize_turn_text(str(record.get("user_text") or "")),
                sanitize_turn_text(str(record.get("assistant_text") or "")),
            )
        except Exception:
            continue
        store.mark_synced(str(record.get("turn_id") or ""))
        count += 1
    return count


def prefetch_next(query: str, ref: Any, config: Mapping[str, Any]) -> bool:
    if str(config.get("recall_mode") or "hybrid") == "tools":
        return False
    if not query.strip():
        return False
    try:
        context = HonchoAdapter(config).fetch_context(ref, query=query)
    except Exception:
        return False
    if not context:
        return False
    HonchoStore.from_config(config).write_cache(ref, context)
    return True


def tool_call(ctx: Any, config: Mapping[str, Any], name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    ref = session_ref_from_ctx(ctx, config)
    adapter = HonchoAdapter(config)
    if name == "honcho_profile":
        card = args.get("card")
        if card is not None and not isinstance(card, list):
            return {"success": False, "error": "card must be a list of strings."}
        result = adapter.profile(ref, peer=str(args.get("peer") or "user"), card=card)
        return {"success": True, "message": "Honcho profile read." if card is None else "Honcho profile updated.", **result}
    if name == "honcho_search":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "query is required."}
        result = adapter.search(ref, query, peer=str(args.get("peer") or "user"))
        return {"success": True, "message": "Honcho search completed.", "result": result or "No relevant context found."}
    if name == "honcho_context":
        result = adapter.context(ref, peer=str(args.get("peer") or "user"))
        return {"success": True, "message": "Honcho context fetched.", "context": result}
    if name == "honcho_reasoning":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "query is required."}
        result = adapter.reasoning(
            ref,
            query,
            peer=str(args.get("peer") or "user"),
            reasoning_level=str(args.get("reasoning_level") or "").strip() or None,
        )
        return {"success": True, "message": "Honcho reasoning completed.", "result": result or "No result from Honcho."}
    if name == "honcho_conclude":
        conclusion = str(args.get("conclusion") or "").strip()
        delete_id = str(args.get("delete_id") or "").strip()
        if bool(conclusion) == bool(delete_id):
            return {"success": False, "error": "Exactly one of conclusion or delete_id must be provided."}
        ok = adapter.conclude(ref, conclusion=conclusion or None, delete_id=delete_id or None, peer=str(args.get("peer") or "user"))
        return {"success": bool(ok), "message": "Honcho conclusion updated." if ok else "Honcho conclusion update failed."}
    return {"success": False, "error": f"Unknown Honcho tool: {name}"}
