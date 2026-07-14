from __future__ import annotations

import json
import urllib.request

import httpx
import pytest

from demiurge.security.url_policy import (
    UnsafeUrlError,
    UrlPolicyAsyncTransport,
    UrlPolicyConnectionHandler,
    UrlPolicy,
    UrlPolicyRedirectHandler,
)


def test_url_policy_rejects_private_literal_targets_without_dns():
    resolver_calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolver_calls.append((hostname, port))
        return ("93.184.216.34",)

    policy = UrlPolicy(resolver=resolver)

    decisions = [
        policy.evaluate("http://127.0.0.1/admin"),
        policy.evaluate("http://10.0.0.8/private"),
        policy.evaluate("http://169.254.169.254/latest/meta-data"),
        policy.evaluate("http://[::1]/admin"),
        policy.evaluate("http://[fd00::1]/private"),
    ]

    assert [decision.allowed for decision in decisions] == [False] * 5
    assert [decision.reason for decision in decisions] == [
        "private_address",
        "private_address",
        "metadata_address",
        "private_address",
        "private_address",
    ]
    assert resolver_calls == []


@pytest.mark.parametrize(
    "hostname",
    [
        "2130706433",
        "0x7f000001",
        "017700000001",
        "0x7f.0x0.0x0.0x1",
        "0177.0.0.1",
        "127.1",
        "127.0.1",
    ],
)
def test_url_policy_rejects_legacy_encoded_loopback_without_dns(hostname):
    resolver_calls: list[tuple[str, int]] = []

    def resolver(value: str, port: int) -> tuple[str, ...]:
        resolver_calls.append((value, port))
        return ("93.184.216.34",)

    decision = UrlPolicy(resolver=resolver).evaluate(
        f"http://{hostname}/admin"
    )

    assert decision.allowed is False
    assert decision.reason == "private_address"
    assert decision.resolved_addresses == ("127.0.0.1",)
    assert resolver_calls == []


def test_url_policy_rejects_unsupported_scheme_missing_host_and_invalid_port():
    policy = UrlPolicy(
        resolver=lambda hostname, port: ("93.184.216.34",),
    )

    decisions = [
        policy.evaluate("file:///etc/passwd"),
        policy.evaluate("https:///missing-host"),
        policy.evaluate("https://public.example:99999/path"),
    ]

    assert [decision.reason for decision in decisions] == [
        "unsupported_scheme",
        "missing_hostname",
        "invalid_url",
    ]


def test_url_policy_requires_every_dns_answer_to_be_public_and_fails_closed():
    answers = {
        "public.example": ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
        "mixed.example": ("93.184.216.34", "10.0.0.8"),
        "empty.example": (),
        "invalid.example": ("not-an-ip",),
    }

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        if hostname == "missing.example":
            raise OSError("synthetic DNS failure")
        return answers[hostname]

    policy = UrlPolicy(resolver=resolver)

    public = policy.evaluate("https://public.example/resource")
    mixed = policy.evaluate("https://mixed.example/resource")
    empty = policy.evaluate("https://empty.example/resource")
    invalid = policy.evaluate("https://invalid.example/resource")
    missing = policy.evaluate("https://missing.example/resource")

    assert public.allowed is True
    assert public.reason == "allowed"
    assert public.resolved_addresses == answers["public.example"]
    assert mixed.allowed is False
    assert mixed.reason == "private_address"
    assert empty.reason == "dns_no_addresses"
    assert invalid.reason == "dns_invalid_address"
    assert missing.reason == "dns_failure"


def test_url_policy_blocks_internal_hostnames_cgnat_and_metadata_floor():
    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return ("127.0.0.1",)
        raise AssertionError(f"blocked hostname unexpectedly resolved: {hostname}:{port}")

    policy = UrlPolicy(resolver=resolver)

    decisions = [
        policy.evaluate("http://localhost/admin"),
        policy.evaluate("http://service.localhost/admin"),
        policy.evaluate("http://metadata.google.internal/computeMetadata/v1"),
        policy.evaluate("http://metadata.goog/computeMetadata/v1"),
        policy.evaluate("http://100.64.0.1/private"),
        policy.evaluate("http://[::ffff:127.0.0.1]/admin"),
    ]

    assert [decision.reason for decision in decisions] == [
        "private_hostname",
        "private_hostname",
        "metadata_hostname",
        "metadata_hostname",
        "private_address",
        "private_address",
    ]

    private_policy = UrlPolicy(resolver=resolver, allow_private=True)
    assert private_policy.evaluate("http://10.0.0.8/private").allowed is True
    assert private_policy.evaluate("http://localhost/admin").allowed is True
    assert private_policy.evaluate("http://169.254.169.254/latest").reason == "metadata_address"
    assert private_policy.evaluate("http://metadata.google.internal/latest").reason == "metadata_hostname"


def test_url_policy_audit_view_omits_url_credentials_path_query_and_fragment():
    policy = UrlPolicy(
        resolver=lambda hostname, port: ("93.184.216.34",),
    )

    decision = policy.evaluate(
        "https://user:SENSITIVE_PASSWORD@public.example:8443/"
        "SENSITIVE_PATH?token=SENSITIVE_QUERY#SENSITIVE_FRAGMENT"
    )
    audit = decision.audit_view()
    serialized = json.dumps(audit, sort_keys=True)

    assert decision.allowed is True
    assert audit["target"] == "https://public.example:8443"
    assert audit["resolved_addresses"] == ["93.184.216.34"]
    assert "user" not in serialized
    assert "SENSITIVE" not in serialized


def test_url_redirect_handler_reresolves_and_blocks_public_to_private_change():
    resolver_calls = 0

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1:
            return ("93.184.216.34",)
        return ("10.0.0.8",)

    policy = UrlPolicy(resolver=resolver)
    initial = policy.evaluate("https://public.example/start")
    handler = UrlPolicyRedirectHandler(policy)

    assert initial.allowed is True
    with pytest.raises(UnsafeUrlError) as caught:
        handler.redirect_request(
            urllib.request.Request("https://public.example/start"),
            None,
            302,
            "Found",
            {"location": "/next"},
            "https://public.example/next?token=SENSITIVE_REDIRECT_SECRET",
        )

    assert caught.value.decision.reason == "private_address"
    assert "SENSITIVE_REDIRECT_SECRET" not in str(caught.value)
    assert resolver_calls == 2


def test_url_connection_handler_pins_validated_ip_and_preserves_https_authority():
    captured: dict[str, object] = {}

    class Response:
        status = 200
        reason = "OK"
        headers: dict[str, str] = {}
        sock = None

    class Connection:
        def __init__(
            self,
            host: str,
            *,
            port: int,
            timeout: float,
            context: object,
            server_hostname: str,
        ) -> None:
            captured.update(
                connect_host=host,
                connect_port=port,
                timeout=timeout,
                context=context,
                server_hostname=server_hostname,
            )
            self.sock = None

        def set_debuglevel(self, level: int) -> None:
            captured["debuglevel"] = level

        def set_tunnel(self, host: str, headers: dict[str, str]) -> None:
            raise AssertionError(f"unexpected proxy tunnel: {host} {headers}")

        def request(
            self,
            method: str,
            path: str,
            body: object,
            headers: dict[str, str],
            *,
            encode_chunked: bool,
        ) -> None:
            captured.update(
                method=method,
                path=path,
                headers=headers,
                encode_chunked=encode_chunked,
            )

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            captured["closed"] = True

    policy = UrlPolicy(
        resolver=lambda hostname, port: ("93.184.216.34",),
    )
    handler = UrlPolicyConnectionHandler(
        policy,
        https_connection_factory=Connection,
    )
    request = urllib.request.Request(
        "https://public.example:8443/path?secret=SENSITIVE",
        method="GET",
    )
    request.timeout = 3

    response = handler.https_open(request)

    assert response.status == 200
    assert captured["connect_host"] == "93.184.216.34"
    assert captured["connect_port"] == 8443
    assert captured["server_hostname"] == "public.example"
    assert captured["path"] == "/path?secret=SENSITIVE"
    assert captured["headers"]["Host"] == "public.example:8443"  # type: ignore[index]


def test_url_connection_handler_replaces_default_urllib_dns_handlers():
    handler = UrlPolicyConnectionHandler(
        UrlPolicy(
            resolver=lambda hostname, port: ("93.184.216.34",),
        )
    )

    opener = urllib.request.build_opener(handler)

    assert handler in opener.handlers
    assert not any(
        type(candidate)
        in {urllib.request.HTTPHandler, urllib.request.HTTPSHandler}
        for candidate in opener.handlers
    )


@pytest.mark.asyncio
async def test_url_async_transport_pins_validated_ip_and_preserves_https_authority():
    captured: dict[str, object] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        captured.update(
            url=str(request.url),
            host=request.headers["Host"],
            sni_hostname=request.extensions.get("sni_hostname"),
        )
        return httpx.Response(200, content=b"ok")

    inner = httpx.MockTransport(handle)
    transport = UrlPolicyAsyncTransport(
        UrlPolicy(
            resolver=lambda hostname, port: ("93.184.216.34",),
        ),
        transport_factory=lambda: inner,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(
            "https://public.example:8443/path?secret=SENSITIVE"
        )

    assert response.status_code == 200
    assert response.request.url.host == "public.example"
    assert captured == {
        "url": "https://93.184.216.34:8443/path?secret=SENSITIVE",
        "host": "public.example:8443",
        "sni_hostname": "public.example",
    }


def test_url_policy_normalizes_unicode_hostname_before_resolution_and_audit():
    resolver_calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolver_calls.append((hostname, port))
        return ("93.184.216.34",)

    decision = UrlPolicy(resolver=resolver).evaluate(
        "https://bücher.example./path"
    )

    assert decision.allowed is True
    assert decision.hostname == "xn--bcher-kva.example"
    assert decision.audit_view()["target"] == "https://xn--bcher-kva.example"
    assert resolver_calls == [("xn--bcher-kva.example", 443)]


def test_private_url_opt_in_does_not_allow_unspecified_multicast_or_reserved_targets():
    policy = UrlPolicy(
        resolver=lambda hostname, port: ("93.184.216.34",),
        allow_private=True,
    )

    decisions = [
        policy.evaluate("http://0.0.0.0/"),
        policy.evaluate("http://0.0.0.1/"),
        policy.evaluate("http://224.0.0.1/"),
        policy.evaluate("http://240.0.0.1/"),
        policy.evaluate("http://192.0.2.1/"),
        policy.evaluate("http://198.18.0.1/"),
        policy.evaluate("http://[2001:db8::1]/"),
        policy.evaluate("http://[::]/"),
        policy.evaluate("http://[ff02::1]/"),
    ]

    assert [decision.reason for decision in decisions] == [
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
        "non_routable_address",
    ]
