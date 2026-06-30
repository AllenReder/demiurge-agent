import pytest

from demiurge.channels.http import require_public_http_url
from demiurge.channels.webhook_server import WebhookHttpServer


def test_callback_url_must_be_https():
    with pytest.raises(ValueError, match="https"):
        require_public_http_url("http://example.com/callback")


def test_webhook_server_rejects_oversized_body(monkeypatch):
    server = WebhookHttpServer(host="127.0.0.1", port=0, path="/hook", handler=lambda request: None, max_body_bytes=4)
    httpd = server._make_server(None)  # type: ignore[arg-type]
    handler_cls = httpd.RequestHandlerClass
    sent = []

    handler = object.__new__(handler_cls)
    handler.path = "/hook"
    handler.headers = {"Content-Length": "5"}
    handler._send_json = lambda status, payload: sent.append((status, payload))

    handler.do_POST()

    assert sent == [(413, {"error": "request body too large"})]
    httpd.server_close()
