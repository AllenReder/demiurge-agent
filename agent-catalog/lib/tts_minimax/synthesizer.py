from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://api.minimaxi.com/v1/t2a_v2"
DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_VOICE_ID = "male-qn-qingse"
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_OUTPUT_FORMAT = "hex"

REQUEST_FIELDS = (
    "pronunciation_dict",
    "timbre_weights",
    "language_boost",
    "voice_modify",
    "subtitle_enable",
    "subtitle_type",
    "output_format",
    "aigc_watermark",
)

MEDIA_TYPES = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "pcm": "audio/L16",
    "pcmu_raw": "audio/basic",
    "pcmu_wav": "audio/wav",
    "wav": "audio/wav",
}


@dataclass(frozen=True)
class SynthesisResult:
    path: Path
    media_type: str
    metadata: dict[str, Any]


def synthesize_to_file(text: str, config: Mapping[str, Any], *, workspace: Path, turn_id: str) -> SynthesisResult:
    api_key = _resolve_secret(config, "api_key", "api_key_env", default_env="DEMIURGE_MINIMAX_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_MINIMAX_API_KEY")
        raise ValueError(f"MiniMax TTS API key is not configured; set {env_name} or config.api_key")

    audio_setting = _mapping(config.get("audio_setting"))
    audio_format = str(audio_setting.get("format") or DEFAULT_AUDIO_FORMAT).strip().lower()
    output_path = _output_path(config, workspace=workspace, turn_id=turn_id, audio_format=audio_format)
    payload = _build_payload(text, config, audio_setting=audio_setting)
    url = _url_with_group_id(str(config.get("base_url") or DEFAULT_BASE_URL), config)
    response = _post_json(url, payload, api_key=api_key, timeout=_timeout(config))
    data = _parse_json_response(response)
    _raise_for_minimax_error(data)

    audio_value = _response_audio_value(data)
    output_format = str(payload.get("output_format") or DEFAULT_OUTPUT_FORMAT).strip().lower()
    if output_format == "hex":
        audio_bytes = _decode_hex_audio(audio_value)
        output_path.write_bytes(audio_bytes)
    elif output_format == "url":
        _download_audio(str(audio_value), output_path, timeout=_timeout(config))
    else:
        raise ValueError(f"unsupported MiniMax output_format: {output_format}")

    metadata = {
        "provider": "tts_minimax",
        "model": payload.get("model"),
        "voice_id": _mapping(payload.get("voice_setting")).get("voice_id"),
        "trace_id": data.get("trace_id"),
        "extra_info": data.get("extra_info"),
        "subtitle_file": _mapping(data.get("data")).get("subtitle_file"),
        "output_format": output_format,
        "audio_format": audio_format,
    }
    return SynthesisResult(
        path=output_path,
        media_type=str(config.get("media_type") or MEDIA_TYPES.get(audio_format, "application/octet-stream")),
        metadata=_drop_none(metadata),
    )


def _build_payload(text: str, config: Mapping[str, Any], *, audio_setting: Mapping[str, Any]) -> dict[str, Any]:
    voice_setting = dict(_mapping(config.get("voice_setting")))
    voice_setting.setdefault("voice_id", str(config.get("voice_id") or DEFAULT_VOICE_ID))
    payload: dict[str, Any] = {
        "model": str(config.get("model") or DEFAULT_MODEL),
        "text": text,
        "stream": False,
        "voice_setting": voice_setting,
    }
    if audio_setting:
        payload["audio_setting"] = dict(audio_setting)
    if config.get("output_format"):
        payload["output_format"] = str(config.get("output_format"))
    for field in REQUEST_FIELDS:
        if field in config:
            payload[field] = config.get(field)
    return _drop_none(payload)


def _post_json(url: str, payload: Mapping[str, Any], *, api_key: str, timeout: float) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = _safe_error_body(exc)
        raise RuntimeError(f"MiniMax TTS HTTP error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax TTS request failed: {exc.reason}") from exc


def _parse_json_response(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("MiniMax TTS returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("MiniMax TTS returned a non-object JSON response")
    return parsed


def _raise_for_minimax_error(response: Mapping[str, Any]) -> None:
    base_resp = _mapping(response.get("base_resp"))
    status_code = base_resp.get("status_code", 0)
    if status_code not in {0, "0", None}:
        status_msg = str(base_resp.get("status_msg") or "unknown error")
        raise RuntimeError(f"MiniMax TTS API error (code {status_code}): {status_msg}")


def _response_audio_value(response: Mapping[str, Any]) -> str:
    data = _mapping(response.get("data"))
    audio = data.get("audio")
    if not audio:
        raise RuntimeError("MiniMax TTS returned empty audio data")
    return str(audio)


def _decode_hex_audio(value: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise RuntimeError("MiniMax TTS returned invalid hex audio data") from exc


def _download_audio(url: str, output_path: Path, *, timeout: float) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("MiniMax TTS returned an invalid audio URL")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            output_path.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        detail = _safe_error_body(exc)
        raise RuntimeError(f"MiniMax TTS audio download HTTP error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax TTS audio download failed: {exc.reason}") from exc


def _resolve_secret(config: Mapping[str, Any], value_key: str, env_key: str, *, default_env: str = "") -> str:
    direct = str(config.get(value_key) or "").strip()
    if direct:
        return direct
    env_name = str(config.get(env_key) or default_env).strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def _url_with_group_id(base_url: str, config: Mapping[str, Any]) -> str:
    group_id = _resolve_secret(config, "group_id", "group_id_env")
    if not group_id:
        return base_url
    parsed = urllib.parse.urlparse(base_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == "GroupId" for key, _ in query):
        return base_url
    query.append(("GroupId", group_id))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _output_path(config: Mapping[str, Any], *, workspace: Path, turn_id: str, audio_format: str) -> Path:
    output_dir = Path(str(config.get("output_dir") or ".demiurge-tts"))
    if not output_dir.is_absolute():
        output_dir = workspace / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    template = str(config.get("filename_template") or "{turn_id}.{format}")
    filename = template.replace("{turn_id}", _safe_filename(turn_id)).replace("{format}", _safe_filename(audio_format))
    path = output_dir / filename
    if path.suffix == "":
        path = path.with_suffix(f".{_safe_filename(audio_format)}")
    return path


def _timeout(config: Mapping[str, Any]) -> float:
    value = config.get("timeout_seconds")
    if isinstance(value, bool):
        return 60.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 60.0
    return parsed if parsed > 0 else 60.0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _drop_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value if item is not None]
    return value


def _safe_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    return body[:500] or exc.reason or "no response body"


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value) or "value"
