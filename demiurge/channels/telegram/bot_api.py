from __future__ import annotations

import http.client
import json
import mimetypes
import os
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)
_SAFE_RETRY_METHODS = {"getUpdates", "getFile", "deleteWebhook", "sendChatAction"}


class TelegramApiError(RuntimeError):
    def __init__(
        self,
        method: str,
        error_code: int | None,
        description: str,
        parameters: dict[str, Any] | None = None,
    ):
        self.method = method
        self.error_code = error_code
        self.description = description
        self.parameters = dict(parameters or {})
        super().__init__(f"telegram {method} failed ({error_code}): {description}")

    @property
    def retry_after(self) -> float | None:
        value = self.parameters.get("retry_after")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class TelegramBotApi:
    def __init__(self, token: str, *, base_url: str = "https://api.telegram.org"):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def get_updates(self, *, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            params["offset"] = offset
        data = self._request("getUpdates", params, retry_policy="safe")
        result = data.get("result", [])
        return result if isinstance(result, list) else []

    def get_file(self, file_id: str) -> dict[str, Any]:
        data = self._request("getFile", {"file_id": file_id}, retry_policy="safe")
        result = data.get("result", {})
        return result if isinstance(result, dict) else {}

    def download_file(self, file_path: str, *, timeout: int | float = 30) -> bytes:
        path = urllib.parse.quote(file_path.lstrip("/"), safe="/")
        request = urllib.request.Request(f"{self.base_url}/file/bot{self.token}/{path}", method="GET")
        try:
            response = self._urlopen_with_retry(
                request,
                timeout,
                method="downloadFile",
                retry_policy="safe",
            )
        except urllib.error.HTTPError as exc:
            raise self._api_error_from_http_error("downloadFile", exc) from exc
        with response:
            return response.read()

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self._request(
            "deleteWebhook",
            {"drop_pending_updates": "true" if drop_pending_updates else "false"},
            retry_policy="safe",
        )

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._request("sendMessage", params)

    def edit_message_text(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._request("editMessageText", params)

    def edit_message_reply_markup(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self._request("editMessageReplyMarkup", params)

    def send_rich_message(
        self,
        *,
        chat_id: int | str,
        markdown: str,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": json.dumps({"markdown": markdown}, ensure_ascii=False),
        }
        if reply_to_message_id is not None:
            params["reply_parameters"] = json.dumps({"message_id": reply_to_message_id}, ensure_ascii=False)
        return self._request("sendRichMessage", params)

    def send_chat_action(self, *, chat_id: int | str, action: str = "typing") -> dict[str, Any]:
        return self._request("sendChatAction", {"chat_id": chat_id, "action": action}, retry_policy="safe")

    def send_photo(
        self,
        *,
        chat_id: int | str,
        photo: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self._send_media(
            "sendPhoto",
            "photo",
            chat_id=chat_id,
            value=photo,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def send_audio(
        self,
        *,
        chat_id: int | str,
        audio: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self._send_media(
            "sendAudio",
            "audio",
            chat_id=chat_id,
            value=audio,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def send_voice(
        self,
        *,
        chat_id: int | str,
        voice: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self._send_media(
            "sendVoice",
            "voice",
            chat_id=chat_id,
            value=voice,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def send_video(
        self,
        *,
        chat_id: int | str,
        video: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self._send_media(
            "sendVideo",
            "video",
            chat_id=chat_id,
            value=video,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def send_document(
        self,
        *,
        chat_id: int | str,
        document: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return self._send_media(
            "sendDocument",
            "document",
            chat_id=chat_id,
            value=document,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    def answer_callback_query(self, *, callback_query_id: str, text: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        return self._request("answerCallbackQuery", params)

    def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        return self._request("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)})

    def _request(self, method: str, params: dict[str, Any], *, retry_policy: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/bot{self.token}/{method}"
        body = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        timeout = max(int(params.get("timeout", 5)) + 5, 10)
        try:
            response = self._urlopen_with_retry(
                request,
                timeout,
                method=method,
                retry_policy=retry_policy or self._retry_policy_for_method(method),
            )
        except urllib.error.HTTPError as exc:
            raise self._api_error_from_http_error(method, exc) from exc
        with response:
            return self._parse_response(method, response.read())

    def _send_media(
        self,
        method: str,
        field_name: str,
        *,
        chat_id: int | str,
        value: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        path = Path(value)
        if path.exists() and path.is_file():
            return self._request_multipart(method, params, field_name=field_name, path=path)
        params[field_name] = value
        return self._request(method, params)

    def _request_multipart(
        self,
        method: str,
        params: dict[str, Any],
        *,
        field_name: str,
        path: Path,
    ) -> dict[str, Any]:
        boundary = f"----demiurge-{os.urandom(12).hex()}"
        body = bytearray()

        def add_line(value: bytes) -> None:
            body.extend(value)
            body.extend(b"\r\n")

        for key, value in params.items():
            if value is None:
                continue
            add_line(f"--{boundary}".encode("utf-8"))
            add_line(f'Content-Disposition: form-data; name="{key}"'.encode("utf-8"))
            add_line(b"")
            add_line(str(value).encode("utf-8"))

        filename = path.name
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        add_line(f"--{boundary}".encode("utf-8"))
        add_line(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode("utf-8")
        )
        add_line(f"Content-Type: {media_type}".encode("utf-8"))
        add_line(b"")
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
        add_line(f"--{boundary}--".encode("utf-8"))

        request = urllib.request.Request(
            f"{self.base_url}/bot{self.token}/{method}",
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            response = self._urlopen_with_retry(request, 30, method=method, retry_policy="send")
        except urllib.error.HTTPError as exc:
            raise self._api_error_from_http_error(method, exc) from exc
        with response:
            return self._parse_response(method, response.read())

    def _parse_response(self, method: str, payload: bytes) -> dict[str, Any]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict) and data.get("ok") is False:
            error_code = data.get("error_code")
            raise TelegramApiError(
                method,
                int(error_code) if isinstance(error_code, int) else None,
                str(data.get("description") or "telegram api error"),
                data.get("parameters") if isinstance(data.get("parameters"), dict) else None,
            )
        return data

    def _api_error_from_http_error(self, method: str, exc: urllib.error.HTTPError) -> Exception:
        try:
            return self._api_error_from_payload(method, exc.read())
        except Exception:
            return exc

    def _api_error_from_payload(self, method: str, payload: bytes) -> TelegramApiError:
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict) or data.get("ok") is not False:
            raise ValueError("telegram error payload was not an ok=false object")
        error_code = data.get("error_code")
        return TelegramApiError(
            method,
            int(error_code) if isinstance(error_code, int) else None,
            str(data.get("description") or "telegram api error"),
            data.get("parameters") if isinstance(data.get("parameters"), dict) else None,
        )

    def _urlopen_with_retry(
        self,
        request: urllib.request.Request,
        timeout: int | float,
        *,
        method: str,
        retry_policy: str,
    ):
        delays = _RETRY_DELAYS_SECONDS if retry_policy in {"safe", "send"} else ()
        for attempt in range(len(delays) + 1):
            try:
                return urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError:
                raise
            except _TRANSIENT_TRANSPORT_ERRORS as exc:
                if attempt >= len(delays):
                    raise
                if retry_policy == "send" and _looks_like_response_timeout(exc):
                    raise
                delay = delays[attempt] + random.uniform(0, 0.1)
                time.sleep(delay)

        raise RuntimeError(f"telegram {method} retry loop exited unexpectedly")

    def _retry_policy_for_method(self, method: str) -> str:
        return "safe" if method in _SAFE_RETRY_METHODS else "send"


_TRANSIENT_TRANSPORT_ERRORS = (
    urllib.error.URLError,
    ssl.SSLError,
    TimeoutError,
    ConnectionError,
    socket.timeout,
    http.client.RemoteDisconnected,
)


def _looks_like_response_timeout(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "read timed out" in text or "write timed out" in text or "readtimeout" in text or "writetimeout" in text
