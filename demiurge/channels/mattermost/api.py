from __future__ import annotations

from typing import Any

from demiurge.channels.http import json_request


class MattermostApi:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        incoming_webhook_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.incoming_webhook_url = incoming_webhook_url

    def post_message(self, *, channel_id: str, text: str, root_id: str | None = None) -> dict[str, Any]:
        if self.base_url and self.token:
            payload: dict[str, Any] = {"channel_id": channel_id, "message": text}
            if root_id:
                payload["root_id"] = root_id
            return json_request(
                f"{self.base_url}/api/v4/posts",
                payload=payload,
                token=self.token,
                allow_private=True,
            )
        if self.incoming_webhook_url:
            payload = {"text": text}
            if channel_id:
                payload["channel_id"] = channel_id
            return json_request(self.incoming_webhook_url, payload=payload, allow_private=True)
        raise RuntimeError("mattermost channel requires base_url+token or incoming_webhook_url")
