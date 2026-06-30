from __future__ import annotations

import urllib.parse
from typing import Any

from demiurge.channels.http import json_request


class MatrixApi:
    def __init__(self, *, homeserver_url: str, access_token: str) -> None:
        self.homeserver_url = homeserver_url.rstrip("/")
        self.access_token = access_token

    def sync(self, *, since: str | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
        query: dict[str, Any] = {"timeout": timeout_ms}
        if since:
            query["since"] = since
        url = f"{self.homeserver_url}/_matrix/client/v3/sync?{urllib.parse.urlencode(query)}"
        return json_request(url, method="GET", payload=None, token=self.access_token, allow_private=True, timeout=max(timeout_ms / 1000 + 10, 15))

    def send_message(self, *, room_id: str, body: str, txn_id: str) -> dict[str, Any]:
        room = urllib.parse.quote(room_id, safe="")
        txn = urllib.parse.quote(txn_id, safe="")
        url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}"
        return json_request(
            url,
            method="PUT",
            payload={"msgtype": "m.text", "body": body},
            token=self.access_token,
            allow_private=True,
        )
