from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs

from demiurge.security.redaction import RedactionView, redact_exception_message


logger = logging.getLogger(__name__)


class WebhookHttpServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        handler: Callable[[dict[str, Any]], Any],
        max_body_bytes: int = 1_048_576,
        read_timeout_seconds: float = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path if path.startswith("/") else f"/{path}"
        self.handler = handler
        self.max_body_bytes = max_body_bytes
        self.read_timeout_seconds = read_timeout_seconds
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        server = self._make_server(loop)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name=f"demiurge-webhook-{self.port}", daemon=True)
        self._thread.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            server.shutdown()
            raise
        finally:
            server.server_close()

    def _make_server(self, loop: asyncio.AbstractEventLoop) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DemiurgeWebhook/1.0"

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != owner.path:
                    self._send_json(404, {"error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self._send_json(400, {"error": "invalid content length"})
                    return
                if length > owner.max_body_bytes:
                    self._send_json(413, {"error": "request body too large"})
                    return
                self.connection.settimeout(owner.read_timeout_seconds)
                try:
                    raw_body = self.rfile.read(length)
                except TimeoutError:
                    self._send_json(408, {"error": "request body timed out"})
                    return
                content_type = self.headers.get("Content-Type") or ""
                request: dict[str, Any] | None = None
                try:
                    payload = _parse_body(raw_body, content_type)
                    request = {
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": payload,
                        "raw_body": raw_body,
                        "path": self.path,
                        "client": self.client_address[0] if self.client_address else None,
                    }
                    future = asyncio.run_coroutine_threadsafe(_call_handler(owner.handler, request), loop)
                    result = future.result(timeout=120)
                except Exception as exc:
                    safe_error = redact_exception_message(
                        exc,
                        view=RedactionView.OPERATOR,
                        context=request,
                    )
                    logger.error("webhook request failed: %s", safe_error)
                    self._send_json(400, {"error": safe_error})
                    return
                status = int(result.get("status", 200)) if isinstance(result, dict) else 200
                body = result.get("body", result) if isinstance(result, dict) else result
                self._send_json(status, body if isinstance(body, dict) else {"result": body})

            def log_message(self, fmt: str, *args: object) -> None:
                logger.debug("webhook http: " + fmt, *args)

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer((self.host, self.port), Handler)


async def _call_handler(handler: Callable[[dict[str, Any]], Any], request: dict[str, Any]) -> dict[str, Any]:
    result = handler(request)
    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
        result = await result
    if result is None:
        return {"status": 202, "body": {"ok": True}}
    return result if isinstance(result, dict) else {"status": 200, "body": result}


def _parse_body(raw_body: bytes, content_type: str) -> dict[str, Any]:
    if "application/json" in content_type:
        parsed = json.loads(raw_body.decode("utf-8") or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("JSON webhook body must be an object")
        return parsed
    if "application/x-www-form-urlencoded" in content_type:
        values = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
        return {key: item[-1] if len(item) == 1 else item for key, item in values.items()}
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return {"text": raw_body.decode("utf-8", errors="replace")}
    if not isinstance(parsed, dict):
        raise ValueError("webhook body must be an object")
    return parsed
