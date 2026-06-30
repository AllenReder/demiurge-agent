from __future__ import annotations

from typing import Any

from demiurge.channels.http import json_request


class SlackApi:
    def __init__(self, token: str, *, base_url: str = "https://slack.com/api") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = json_request(
            f"{self.base_url}/chat.postMessage",
            payload=payload,
            token=self.token,
            allow_private=True,
        )
        if data.get("ok") is False:
            raise RuntimeError(f"Slack chat.postMessage failed: {data.get('error') or data}")
        return data
