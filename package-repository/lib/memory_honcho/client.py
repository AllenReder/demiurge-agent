from __future__ import annotations

import os
from typing import Any, Mapping

from .config import credential
from .session import SessionRef


class HonchoUnavailable(RuntimeError):
    pass


class HonchoAdapter:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self.client = self._build_client(config)

    def fetch_context(self, ref: SessionRef, query: str | None = None) -> dict[str, Any]:
        session = self._session(ref)
        result: dict[str, Any] = {}
        try:
            ctx = session.context(summary=True)
            summary = getattr(getattr(ctx, "summary", None), "content", None)
            if summary:
                result["summary"] = summary
        except Exception:
            pass
        user_ctx = self._peer_context(ref.user_peer_id, query=query, target=ref.user_peer_id)
        ai_ctx = self._peer_context(ref.assistant_peer_id, target=ref.assistant_peer_id)
        result.update(
            {
                "representation": user_ctx.get("representation", ""),
                "card": user_ctx.get("card", ""),
                "ai_representation": ai_ctx.get("representation", ""),
                "ai_card": ai_ctx.get("card", ""),
            }
        )
        return {key: value for key, value in result.items() if value}

    def sync_turn(self, ref: SessionRef, user_text: str, assistant_text: str) -> None:
        session = self._session(ref)
        user_peer = self._peer(ref.user_peer_id)
        assistant_peer = self._peer(ref.assistant_peer_id)
        messages = []
        if user_text:
            messages.append(_peer_message(user_peer, user_text))
        if assistant_text:
            messages.append(_peer_message(assistant_peer, assistant_text))
        if messages:
            session.add_messages(messages)

    def profile(self, ref: SessionRef, peer: str = "user", card: list[str] | None = None) -> dict[str, Any]:
        peer_id = self._resolve_peer(ref, peer)
        peer_obj = self._peer(peer_id)
        if card is not None:
            setter = getattr(peer_obj, "set_card", None)
            if not callable(setter):
                raise HonchoUnavailable("Honcho peer card update is not supported by this SDK version")
            updated = setter(card)
            return {"card": updated if updated is not None else card}
        return {"card": _normalize_card(_call_first(peer_obj, ("get_card", "card"), default=[]))}

    def search(self, ref: SessionRef, query: str, peer: str = "user") -> str:
        peer_id = self._resolve_peer(ref, peer)
        ctx = self._peer_context(peer_id, query=query, target=peer_id)
        parts = [str(ctx.get("representation") or "").strip(), str(ctx.get("card") or "").strip()]
        return "\n\n".join(part for part in parts if part)

    def context(self, ref: SessionRef, peer: str = "user") -> dict[str, Any]:
        context = self.fetch_context(ref)
        if peer == "ai":
            return {
                "representation": context.get("ai_representation", ""),
                "card": context.get("ai_card", ""),
                "summary": context.get("summary", ""),
            }
        return context

    def reasoning(self, ref: SessionRef, query: str, peer: str = "user", reasoning_level: str | None = None) -> str:
        peer_id = self._resolve_peer(ref, peer)
        peer_obj = self._peer(peer_id)
        chat = getattr(peer_obj, "chat", None)
        if not callable(chat):
            raise HonchoUnavailable("Honcho reasoning is not supported by this SDK version")
        kwargs = {"reasoning_level": reasoning_level} if reasoning_level else {}
        return str(chat(query, **kwargs) or "")

    def conclude(self, ref: SessionRef, conclusion: str | None = None, delete_id: str | None = None, peer: str = "user") -> bool:
        peer_obj = self._peer(self._resolve_peer(ref, peer))
        if conclusion:
            creator = getattr(peer_obj, "create_conclusion", None) or getattr(peer_obj, "conclude", None)
            if callable(creator):
                creator(conclusion)
                return True
            card = _normalize_card(_call_first(peer_obj, ("get_card", "card"), default=[]))
            card.append(conclusion)
            setter = getattr(peer_obj, "set_card", None)
            if callable(setter):
                setter(card)
                return True
            raise HonchoUnavailable("Honcho conclusion writes are not supported by this SDK version")
        if delete_id:
            deleter = getattr(peer_obj, "delete_conclusion", None)
            if callable(deleter):
                return bool(deleter(delete_id))
            raise HonchoUnavailable("Honcho conclusion deletion is not supported by this SDK version")
        return False

    def _build_client(self, config: Mapping[str, Any]) -> Any:
        api_key = credential(config, "api_key", "api_key_env")
        base_url = credential(config, "base_url", "base_url_env")
        if not api_key and not base_url:
            raise HonchoUnavailable("Honcho is not configured; set HONCHO_API_KEY or install with api_key/base_url.")
        try:
            from honcho import Honcho  # type: ignore
        except ImportError as exc:
            raise HonchoUnavailable("honcho-ai is required for memory_honcho; install honcho-ai or disable this package.") from exc
        kwargs: dict[str, Any] = {
            "workspace_id": str(config.get("workspace") or "demiurge"),
            "api_key": api_key or "local",
        }
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        timeout = config.get("timeout_seconds")
        if timeout:
            kwargs["timeout"] = int(timeout)
        environment = os.environ.get("HONCHO_ENVIRONMENT")
        if environment:
            kwargs["environment"] = environment
        return Honcho(**kwargs)

    def _session(self, ref: SessionRef) -> Any:
        session = self.client.session(ref.session_id)
        add_peers = getattr(session, "add_peers", None)
        if callable(add_peers):
            try:
                add_peers([self._peer(ref.user_peer_id), self._peer(ref.assistant_peer_id)])
            except Exception:
                pass
        return session

    def _peer(self, peer_id: str) -> Any:
        return self.client.peer(peer_id)

    def _peer_context(self, peer_id: str, *, query: str | None = None, target: str | None = None) -> dict[str, str]:
        peer = self._peer(peer_id)
        kwargs: dict[str, Any] = {}
        if query:
            kwargs["search_query"] = query
        if target:
            kwargs["target"] = target
        ctx = None
        try:
            ctx = peer.context(**kwargs) if kwargs else peer.context()
        except Exception:
            ctx = None
        representation = (
            getattr(ctx, "representation", None)
            or getattr(ctx, "peer_representation", None)
            or _call_first(peer, ("representation",), default="")
            or ""
        )
        card = _normalize_card(getattr(ctx, "peer_card", None) or _call_first(peer, ("get_card", "card"), default=[]))
        return {"representation": str(representation or ""), "card": "\n".join(card)}

    def _resolve_peer(self, ref: SessionRef, peer: str) -> str:
        normalized = str(peer or "user").strip()
        if normalized == "user":
            return ref.user_peer_id
        if normalized in {"ai", "assistant"}:
            return ref.assistant_peer_id
        return normalized


def _peer_message(peer: Any, content: str) -> Any:
    maker = getattr(peer, "message", None)
    return maker(content) if callable(maker) else {"peer": getattr(peer, "id", ""), "content": content}


def _normalize_card(card: Any) -> list[str]:
    if not card:
        return []
    if isinstance(card, list):
        return [str(item) for item in card if str(item).strip()]
    return [str(card)]


def _call_first(obj: Any, names: tuple[str, ...], *, default: Any) -> Any:
    for name in names:
        candidate = getattr(obj, name, None)
        if callable(candidate):
            return candidate()
    return default
