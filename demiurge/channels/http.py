from __future__ import annotations

import http.client
import ipaddress
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from demiurge.security.url_policy import (
    PinnedHTTPSConnection,
    UnsafeUrlError,
    UrlPolicy,
)


class HttpRequestError(RuntimeError):
    pass


def json_request(
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    token: str | None = None,
    timeout: float = 30,
    allow_private: bool = False,
) -> dict[str, Any]:
    if not allow_private:
        require_public_http_url(url)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")
    if token:
        request_headers.setdefault("Authorization", f"Bearer {token}")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with _open(request, timeout=timeout, allow_redirects=allow_private) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise HttpRequestError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HttpRequestError(f"HTTP request failed for {url}: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpRequestError(f"HTTP response was not JSON for {url}") from exc
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def form_request(
    url: str,
    *,
    method: str = "POST",
    form: dict[str, Any],
    headers: dict[str, str] | None = None,
    token: str | None = None,
    timeout: float = 30,
    allow_private: bool = False,
) -> dict[str, Any]:
    if not allow_private:
        require_public_http_url(url)
    body = urllib.parse.urlencode(form).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        **dict(headers or {}),
    }
    if token:
        request_headers.setdefault("Authorization", f"Bearer {token}")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with _open(request, timeout=timeout, allow_redirects=allow_private) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise HttpRequestError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HttpRequestError(f"HTTP request failed for {url}: {exc.reason}") from exc
    if not response_body.strip():
        return {}
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return {"text": response_body}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _open(request: urllib.request.Request, *, timeout: float, allow_redirects: bool):
    if allow_redirects:
        return urllib.request.build_opener().open(request, timeout=timeout)
    return _open_public_https(request, timeout=timeout)


def _open_public_https(request: urllib.request.Request, *, timeout: float) -> "_ResponseWrapper":
    url = request.full_url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("callback URL must be https and include a hostname")
    port = parsed.port or 443
    addresses = _public_addresses(parsed.hostname, port)
    if not addresses:
        raise ValueError(f"callback URL hostname could not be resolved: {parsed.hostname}")
    connect_host = str(addresses[0])
    context = ssl.create_default_context()
    connection = PinnedHTTPSConnection(
        connect_host,
        port=port,
        timeout=timeout,
        context=context,
        server_hostname=parsed.hostname,
    )
    path = urllib.parse.urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    headers = dict(request.header_items())
    host_header = parsed.hostname if port == 443 else f"{parsed.hostname}:{port}"
    headers.setdefault("Host", host_header)
    connection.request(request.get_method(), path, body=request.data, headers=headers)
    response = connection.getresponse()
    if 300 <= response.status < 400:
        location = response.getheader("Location") or ""
        body = response.read()
        connection.close()
        raise HttpRequestError(f"HTTP redirect is not allowed for callback URL: {location or response.status}")
    if response.status >= 400:
        detail = response.read().decode("utf-8", errors="replace")
        connection.close()
        raise HttpRequestError(f"HTTP {response.status} for {url}: {detail}")
    return _ResponseWrapper(connection, response)


class _ResponseWrapper:
    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse) -> None:
        self.connection = connection
        self.response = response

    def __enter__(self) -> http.client.HTTPResponse:
        return self.response

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.response.close()
        self.connection.close()


def require_public_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("callback URL must be https and include a hostname")
    _public_addresses(parsed.hostname, parsed.port or 443)


def _public_addresses(hostname: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    try:
        decision = UrlPolicy().require(
            f"https://{rendered_host}:{port}/"
        )
    except UnsafeUrlError as exc:
        raise ValueError(
            f"callback URL rejected by Host policy: {exc.decision.reason}"
        ) from None
    return [
        ipaddress.ip_address(address)
        for address in decision.resolved_addresses
    ]
