from __future__ import annotations

from dataclasses import dataclass
import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request
import wave

import yaml


MEDIA_TYPES = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "mpeg": "audio/mpeg",
    "opus": "audio/ogg",
    "pcm": "audio/L16",
    "wav": "audio/wav",
}


@dataclass(frozen=True)
class SynthesisResult:
    path: Path
    media_type: str
    metadata: dict[str, Any]


def load_synthesis_config(slot_file: str | Path | None = None) -> dict[str, Any]:
    config = _load_yaml_mapping(Path(__file__).with_name("config.yaml"))
    if slot_file is None:
        return config
    return _merge_config(config, _load_yaml_mapping(Path(slot_file).with_name("config.yaml")))


def synthesize_to_file(text: str, config: Mapping[str, Any], *, workspace: Path, turn_id: str) -> SynthesisResult:
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "tts_openai":
        return _synthesize_openai(text, config, workspace=workspace, turn_id=turn_id)
    if provider == "tts_xai":
        return _synthesize_xai(text, config, workspace=workspace, turn_id=turn_id)
    if provider == "tts_gemini":
        return _synthesize_gemini(text, config, workspace=workspace, turn_id=turn_id)
    raise ValueError(f"unsupported TTS provider: {provider or '(missing)'}")


def _synthesize_openai(text: str, config: Mapping[str, Any], *, workspace: Path, turn_id: str) -> SynthesisResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_OPENAI_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_OPENAI_API_KEY")
        raise ValueError(f"OpenAI TTS API key is not configured; set {env_name} or config.api_key")
    response_format = str(config.get("response_format") or "mp3").strip().lower()
    output_path = _output_path(config, workspace=workspace, turn_id=turn_id, audio_format=response_format)
    payload = {
        "model": str(config.get("model") or "gpt-4o-mini-tts"),
        "input": text,
        "voice": str(config.get("voice") or "alloy"),
        "response_format": response_format,
    }
    if config.get("speed") is not None:
        payload["speed"] = _positive_float(config.get("speed"), default=1.0)
    url = _join_url(str(config.get("base_url") or "https://api.openai.com/v1"), str(config.get("endpoint") or "/audio/speech"))
    audio = _post_json_bytes(url, payload, api_key=api_key, provider_label="OpenAI TTS", timeout=_timeout(config))
    output_path.write_bytes(audio)
    return SynthesisResult(
        path=output_path,
        media_type=str(config.get("media_type") or MEDIA_TYPES.get(response_format, "application/octet-stream")),
        metadata=_drop_none(
            {
                "provider": "tts_openai",
                "model": payload["model"],
                "voice": payload["voice"],
                "response_format": response_format,
            }
        ),
    )


def _synthesize_xai(text: str, config: Mapping[str, Any], *, workspace: Path, turn_id: str) -> SynthesisResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_XAI_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_XAI_API_KEY")
        raise ValueError(f"xAI TTS API key is not configured; set {env_name} or config.api_key")
    output_format = _effective_xai_output_format(config)
    codec = _xai_codec(output_format, config)
    output_path = _output_path(config, workspace=workspace, turn_id=turn_id, audio_format=codec)
    payload: dict[str, Any] = {
        "text": text,
        "voice_id": str(config.get("voice_id") or "eve"),
        "language": str(config.get("language") or "en"),
        "output_format": output_format,
    }
    if config.get("speed") is not None:
        payload["speed"] = _clamp(_positive_float(config.get("speed"), default=1.0), 0.7, 1.5)
    if config.get("optimize_streaming_latency") is not None:
        payload["optimize_streaming_latency"] = _clamp_int(config.get("optimize_streaming_latency"), 0, 2)
    url = _join_url(str(config.get("base_url") or "https://api.x.ai/v1"), str(config.get("endpoint") or "/tts"))
    audio = _post_json_bytes(url, payload, api_key=api_key, provider_label="xAI TTS", timeout=_timeout(config))
    output_path.write_bytes(audio)
    return SynthesisResult(
        path=output_path,
        media_type=str(config.get("media_type") or MEDIA_TYPES.get(codec, "application/octet-stream")),
        metadata=_drop_none(
            {
                "provider": "tts_xai",
                "voice_id": payload["voice_id"],
                "language": payload["language"],
                "codec": codec,
            }
        ),
    )


def _synthesize_gemini(text: str, config: Mapping[str, Any], *, workspace: Path, turn_id: str) -> SynthesisResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_GEMINI_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_GEMINI_API_KEY")
        raise ValueError(f"Gemini TTS API key is not configured; set {env_name} or config.api_key")
    output_format = str(config.get("output_format") or "wav").strip().lower()
    output_path = _output_path(config, workspace=workspace, turn_id=turn_id, audio_format=output_format)
    model = str(config.get("model") or "gemini-2.5-flash-preview-tts")
    voice = str(config.get("voice") or "Kore")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": text}],
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice,
                    }
                }
            },
        },
    }
    url = _gemini_generate_url(str(config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"), model)
    raw = _post_json(
        url,
        payload,
        api_key=None,
        provider_label="Gemini TTS",
        timeout=_timeout(config),
        headers={"x-goog-api-key": api_key},
    )
    data = _parse_json(raw, provider_label="Gemini TTS")
    audio_bytes, response_mime = _gemini_audio(data)
    if output_format == "wav" and not audio_bytes.startswith(b"RIFF"):
        audio_bytes = _pcm_to_wav(
            audio_bytes,
            sample_rate=_positive_int(config.get("sample_rate"), 24000),
            channels=_positive_int(config.get("channels"), 1),
            sample_width=_positive_int(config.get("sample_width"), 2),
        )
    effective_media_type = "audio/wav" if output_format == "wav" else response_mime or MEDIA_TYPES.get(output_format, "application/octet-stream")
    output_path.write_bytes(audio_bytes)
    return SynthesisResult(
        path=output_path,
        media_type=str(config.get("media_type") or effective_media_type),
        metadata=_drop_none(
            {
                "provider": "tts_gemini",
                "model": model,
                "voice": voice,
                "output_format": output_format,
                "response_mime_type": response_mime,
            }
        ),
    )


def _post_json_bytes(url: str, payload: Mapping[str, Any], *, api_key: str, provider_label: str, timeout: float) -> bytes:
    return _post_json(url, payload, api_key=api_key, provider_label=provider_label, timeout=timeout)


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    api_key: str | None,
    provider_label: str,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> bytes:
    request_headers = {"Content-Type": "application/json", **dict(headers or {})}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = _safe_error_body(exc)
        raise RuntimeError(f"{provider_label} HTTP error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{provider_label} request failed: {exc.reason}") from exc


def _gemini_generate_url(base_url: str, model: str) -> str:
    base = base_url.rstrip("/")
    quoted_model = urllib.parse.quote(model, safe=".-_")
    return f"{base}/models/{quoted_model}:generateContent"


def _gemini_audio(response: Mapping[str, Any]) -> tuple[bytes, str | None]:
    for candidate in _list(response.get("candidates")):
        content = _mapping(candidate.get("content"))
        for part in _list(content.get("parts")):
            inline = _mapping(part.get("inlineData") or part.get("inline_data"))
            data = inline.get("data")
            if not data:
                continue
            try:
                return base64.b64decode(str(data), validate=True), _optional_str(inline.get("mimeType") or inline.get("mime_type"))
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Gemini TTS returned invalid base64 audio data") from exc
    raise RuntimeError("Gemini TTS returned empty audio data")


def _parse_json(raw: bytes, *, provider_label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider_label} returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{provider_label} returned a non-object JSON response")
    return parsed


def _pcm_to_wav(audio: bytes, *, sample_rate: int, channels: int, sample_width: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(audio)
    return buffer.getvalue()


def _effective_xai_output_format(config: Mapping[str, Any]) -> Any:
    raw = config.get("output_format")
    if raw is None:
        result: dict[str, Any] = {"codec": str(config.get("codec") or "mp3").strip().lower()}
        if config.get("sample_rate") is not None:
            result["sample_rate"] = _positive_int(config.get("sample_rate"), 24000)
        if config.get("bit_rate") is not None:
            result["bit_rate"] = _positive_int(config.get("bit_rate"), 128000)
        return result
    if isinstance(raw, Mapping):
        return _drop_none({str(key): value for key, value in raw.items()})
    if isinstance(raw, list):
        return [_drop_none(item) for item in raw]
    return str(raw)


def _xai_codec(output_format: Any, config: Mapping[str, Any]) -> str:
    if isinstance(output_format, Mapping):
        return str(output_format.get("codec") or config.get("codec") or "mp3").strip().lower()
    if isinstance(output_format, str) and output_format.strip():
        return output_format.strip().lower()
    return str(config.get("codec") or "mp3").strip().lower()


def _resolve_secret(config: Mapping[str, Any], *, default_env: str) -> str:
    direct = str(config.get("api_key") or "").strip()
    if direct:
        return direct
    env_names = [str(config.get("api_key_env") or default_env).strip()]
    env_names.extend(str(item).strip() for item in _list(config.get("fallback_envs")))
    for env_name in env_names:
        if not env_name:
            continue
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def _output_path(config: Mapping[str, Any], *, workspace: Path, turn_id: str, audio_format: str) -> Path:
    workspace = workspace.resolve()
    output_dir = Path(str(config.get("output_dir") or ".demiurge-tts"))
    if output_dir.is_absolute() or ".." in output_dir.parts:
        raise ValueError("TTS output_dir must be workspace-relative")
    output_dir = (workspace / output_dir).resolve()
    if not _is_relative_to(output_dir, workspace):
        raise ValueError("TTS output_dir must resolve inside the workspace")
    template = str(config.get("filename_template") or "{turn_id}.{format}")
    filename = template.replace("{turn_id}", _safe_filename(turn_id)).replace("{format}", _safe_filename(audio_format))
    filename_path = Path(filename)
    if filename_path.is_absolute() or len(filename_path.parts) != 1 or filename_path.name in {"", ".", ".."}:
        raise ValueError("TTS filename_template must render to a single filename")
    path = (output_dir / filename_path.name).resolve()
    if path.suffix == "":
        path = path.with_suffix(f".{_safe_filename(audio_format)}")
    if not _is_relative_to(path, workspace):
        raise ValueError("TTS output path must resolve inside the workspace")
    output_dir.mkdir(parents=True, exist_ok=True)
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


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(str(key))
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _merge_config(current, value)
        else:
            merged[str(key)] = value
    return merged


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    return int(_clamp(float(_positive_int(value, minimum)), float(minimum), float(maximum)))


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
