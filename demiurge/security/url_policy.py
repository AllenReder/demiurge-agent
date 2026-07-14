from __future__ import annotations

import asyncio
import ipaddress
import http.client
import socket
import ssl
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


UrlResolver = Callable[[str, int], tuple[str, ...]]

_METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_OPT_IN_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)
_METADATA_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})


@dataclass(frozen=True, slots=True)
class UrlDecision:
    allowed: bool
    reason: str
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[str, ...] = ()

    def audit_view(self) -> dict[str, object]:
        rendered_host = (
            f"[{self.hostname}]"
            if ":" in self.hostname
            else self.hostname
        )
        default_port = 443 if self.scheme == "https" else 80
        port_suffix = (
            ""
            if self.port == default_port
            else f":{self.port}"
        )
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "target": f"{self.scheme}://{rendered_host}{port_suffix}",
            "scheme": self.scheme,
            "hostname": self.hostname,
            "port": self.port,
            "resolved_addresses": list(self.resolved_addresses),
        }


class UrlPolicy:
    def __init__(
        self,
        *,
        resolver: UrlResolver | None = None,
        allow_private: bool = False,
    ) -> None:
        self._resolver = resolver or resolve_host_addresses
        self.allow_private = bool(allow_private)

    def evaluate(self, url: str) -> UrlDecision:
        raw_url = str(url).strip()
        try:
            parsed = urlsplit(raw_url)
            scheme = parsed.scheme.lower()
            hostname = _normalize_hostname(parsed.hostname or "")
            port = parsed.port or (443 if scheme == "https" else 80)
        except (UnicodeError, ValueError):
            return UrlDecision(False, "invalid_url", "", "", 0)
        if scheme not in {"http", "https"}:
            return UrlDecision(
                False,
                "unsupported_scheme",
                scheme,
                hostname,
                port,
            )
        if not hostname:
            return UrlDecision(False, "missing_hostname", scheme, "", port)
        if hostname in _METADATA_HOSTNAMES:
            return UrlDecision(
                False,
                "metadata_hostname",
                scheme,
                hostname,
                port,
            )
        if (
            not self.allow_private
            and (hostname == "localhost" or hostname.endswith(".localhost"))
        ):
            return UrlDecision(
                False,
                "private_hostname",
                scheme,
                hostname,
                port,
            )
        try:
            address = _parse_ip_address(hostname)
        except ValueError:
            try:
                raw_addresses = tuple(self._resolver(hostname, port))
            except Exception:
                return UrlDecision(
                    False,
                    "dns_failure",
                    scheme,
                    hostname,
                    port,
                )
            if not raw_addresses:
                return UrlDecision(
                    False,
                    "dns_no_addresses",
                    scheme,
                    hostname,
                    port,
                )
            try:
                addresses = tuple(
                    _parse_ip_address(value)
                    for value in raw_addresses
                )
            except ValueError:
                return UrlDecision(
                    False,
                    "dns_invalid_address",
                    scheme,
                    hostname,
                    port,
                )
            resolved_addresses = tuple(str(address) for address in addresses)
            for resolved in addresses:
                reason = _blocked_address_reason(
                    resolved,
                    allow_private=self.allow_private,
                )
                if reason is not None:
                    return UrlDecision(
                        False,
                        reason,
                        scheme,
                        hostname,
                        port,
                        resolved_addresses,
                    )
            return UrlDecision(
                True,
                "allowed",
                scheme,
                hostname,
                port,
                resolved_addresses,
            )
        reason = _blocked_address_reason(
            address,
            allow_private=self.allow_private,
        )
        return UrlDecision(
            reason is None,
            reason or "allowed",
            scheme,
            hostname,
            port,
            (str(address),),
        )

    def require(self, url: str) -> UrlDecision:
        decision = self.evaluate(url)
        if not decision.allowed:
            raise UnsafeUrlError(decision)
        return decision


class UnsafeUrlError(RuntimeError):
    def __init__(self, decision: UrlDecision) -> None:
        self.decision = decision
        audit = decision.audit_view()
        super().__init__(
            "URL blocked by Host policy: "
            f"{decision.reason} ({audit['target']})"
        )


class UrlPolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: UrlPolicy) -> None:
        super().__init__()
        self.policy = policy

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        self.policy.require(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrlPolicyConnectionHandler(
    urllib.request.HTTPHandler,
    urllib.request.HTTPSHandler,
):
    def __init__(
        self,
        policy: UrlPolicy,
        *,
        http_connection_factory: Callable[..., Any] = http.client.HTTPConnection,
        https_connection_factory: Callable[..., Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        urllib.request.AbstractHTTPHandler.__init__(self)
        self.policy = policy
        self.http_connection_factory = http_connection_factory
        self.https_connection_factory = (
            https_connection_factory or PinnedHTTPSConnection
        )
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.last_decision: UrlDecision | None = None

    def http_open(self, req: urllib.request.Request) -> Any:
        decision = self._prepare_request(req)

        def connection_factory(
            _host: str,
            *,
            timeout: float,
            **kwargs: object,
        ) -> Any:
            return self.http_connection_factory(
                decision.resolved_addresses[0],
                port=decision.port,
                timeout=timeout,
                **kwargs,
            )

        return self.do_open(connection_factory, req)

    def https_open(self, req: urllib.request.Request) -> Any:
        decision = self._prepare_request(req)

        def connection_factory(
            _host: str,
            *,
            timeout: float,
            context: ssl.SSLContext,
            **kwargs: object,
        ) -> Any:
            return self.https_connection_factory(
                decision.resolved_addresses[0],
                port=decision.port,
                timeout=timeout,
                context=context,
                server_hostname=decision.hostname,
                **kwargs,
            )

        return self.do_open(
            connection_factory,
            req,
            context=self.ssl_context,
        )

    def _prepare_request(self, req: urllib.request.Request) -> UrlDecision:
        decision = self.policy.require(req.full_url)
        self.last_decision = decision
        req.remove_header("Host")
        req.add_unredirected_header(
            "Host",
            _url_authority(decision),
        )
        return decision


class UrlPolicyAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        policy: UrlPolicy,
        *,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
        decision_sink: Callable[[UrlDecision], None] | None = None,
    ) -> None:
        self.policy = policy
        self.transport_factory = transport_factory or (
            lambda: httpx.AsyncHTTPTransport(trust_env=False)
        )
        self._transports: dict[
            tuple[str, str, int, str],
            httpx.AsyncBaseTransport,
        ] = {}
        self._transport_lock = asyncio.Lock()
        self.decision_sink = decision_sink

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        decision = await asyncio.to_thread(
            self.policy.evaluate,
            str(request.url),
        )
        if self.decision_sink is not None:
            self.decision_sink(decision)
        if not decision.allowed:
            raise UnsafeUrlError(decision)
        connect_host = decision.resolved_addresses[0]
        key = (
            decision.scheme,
            decision.hostname,
            decision.port,
            connect_host,
        )
        transport = await self._transport_for(key)
        headers = request.headers.copy()
        headers["Host"] = _url_authority(decision)
        extensions = dict(request.extensions)
        if decision.scheme == "https":
            extensions["sni_hostname"] = decision.hostname
        pinned_request = httpx.Request(
            request.method,
            request.url.copy_with(host=connect_host),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        async with self._transport_lock:
            transports = list(self._transports.values())
            self._transports.clear()
        for transport in transports:
            await transport.aclose()

    async def _transport_for(
        self,
        key: tuple[str, str, int, str],
    ) -> httpx.AsyncBaseTransport:
        async with self._transport_lock:
            transport = self._transports.get(key)
            if transport is None:
                transport = self.transport_factory()
                self._transports[key] = transport
            return transport


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        *args: Any,
        server_hostname: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._server_hostname = server_hostname

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self._server_hostname,
        )


def _url_authority(decision: UrlDecision) -> str:
    rendered_host = (
        f"[{decision.hostname}]"
        if ":" in decision.hostname
        else decision.hostname
    )
    default_port = 443 if decision.scheme == "https" else 80
    if decision.port == default_port:
        return rendered_host
    return f"{rendered_host}:{decision.port}"


def _parse_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    normalized = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        address = _parse_legacy_ipv4_address(normalized)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _parse_legacy_ipv4_address(value: str) -> ipaddress.IPv4Address:
    parts = value.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        raise ValueError(f"not a legacy IPv4 address: {value}")
    numbers = [_parse_legacy_ipv4_component(part) for part in parts]
    if len(numbers) == 1:
        limits = (0xFFFFFFFF,)
        address_value = numbers[0]
    elif len(numbers) == 2:
        limits = (0xFF, 0xFFFFFF)
        address_value = (numbers[0] << 24) | numbers[1]
    elif len(numbers) == 3:
        limits = (0xFF, 0xFF, 0xFFFF)
        address_value = (
            (numbers[0] << 24)
            | (numbers[1] << 16)
            | numbers[2]
        )
    else:
        limits = (0xFF, 0xFF, 0xFF, 0xFF)
        address_value = (
            (numbers[0] << 24)
            | (numbers[1] << 16)
            | (numbers[2] << 8)
            | numbers[3]
        )
    if any(number > limit for number, limit in zip(numbers, limits, strict=True)):
        raise ValueError(f"legacy IPv4 component out of range: {value}")
    return ipaddress.IPv4Address(address_value)


def _parse_legacy_ipv4_component(value: str) -> int:
    normalized = value.lower()
    if normalized.startswith("0x"):
        if len(normalized) == 2:
            raise ValueError("empty hexadecimal IPv4 component")
        return int(normalized[2:], 16)
    if len(normalized) > 1 and normalized.startswith("0"):
        return int(normalized, 8)
    return int(normalized, 10)


def _normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if not hostname:
        return ""
    try:
        _parse_ip_address(hostname)
    except ValueError:
        return hostname.encode("idna").decode("ascii").lower()
    return hostname


def resolve_host_addresses(hostname: str, port: int) -> tuple[str, ...]:
    addresses: list[str] = []
    for _family, _type, _protocol, _canonical, sockaddr in socket.getaddrinfo(
        hostname,
        port,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
    ):
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _blocked_address_reason(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private: bool,
) -> str | None:
    if address in _METADATA_ADDRESSES or any(
        address in network
        for network in _METADATA_NETWORKS
        if address.version == network.version
    ):
        return "metadata_address"
    if address.is_multicast or address.is_unspecified:
        return "non_routable_address"
    is_opt_in_private = any(
        address in network
        for network in _OPT_IN_PRIVATE_NETWORKS
        if address.version == network.version
    )
    if is_opt_in_private:
        if allow_private:
            return None
        return "private_address"
    if (
        address.is_reserved
        or getattr(address, "is_site_local", False)
    ):
        return "non_routable_address"
    if not address.is_global:
        return "non_routable_address"
    return None
