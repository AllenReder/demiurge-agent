from __future__ import annotations

from dataclasses import dataclass
import base64
import datetime as _datetime
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

import yaml


AUDIO_MIME_PREFIX = "audio/"
DEFAULT_SUPPORTED_MIME_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
    "video/mp4",
    "video/webm",
}
DEFAULT_SUPPORTED_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


@dataclass(frozen=True)
class AttachmentCandidate:
    attachment: Mapping[str, Any]
    index: int
    identifier: str
    filename: str | None
    media_type: str | None
    path: Path | None
    data: bytes | None
    size_bytes: int | None
    duration_seconds: float | None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    metadata: dict[str, Any]


def load_transcription_config(slot_file: str | Path | None = None) -> dict[str, Any]:
    config = _load_yaml_mapping(Path(__file__).with_name("config.yaml"))
    if slot_file is None:
        return config
    return _merge_config(config, _load_yaml_mapping(Path(slot_file).with_name("config.yaml")))


def transcribe_attachments(
    attachments: Sequence[Any],
    config: Mapping[str, Any],
    *,
    workspace: Path,
    session_root: Path | None = None,
) -> TranscriptionResult:
    candidates = audio_attachments(attachments, config, workspace=workspace, session_root=session_root)
    if not candidates:
        raise ValueError(_no_audio_message(attachments, config))
    allow_multiple = _config_bool(config.get("allow_multiple"), default=False)
    if len(candidates) > 1 and not allow_multiple:
        names = ", ".join(candidate.identifier for candidate in candidates[:3])
        raise ValueError(f"multiple supported audio attachments found ({names}); send one voice/audio file at a time")

    max_count = _positive_int(config.get("max_attachments"), default=1 if not allow_multiple else 4)
    selected = candidates[:max_count]
    results = [_transcribe_one(candidate, config) for candidate in selected]
    text = "\n\n".join(result.text for result in results if result.text.strip()).strip()
    metadata = {
        "provider": str(config.get("provider") or "stt"),
        "model": str(config.get("model") or "").strip() or None,
        "attachments": [result.metadata for result in results],
    }
    if len(results) == 1:
        metadata.update(_drop_none({key: value for key, value in results[0].metadata.items() if key != "source"}))
    return TranscriptionResult(text=text, metadata=_drop_none(metadata))


def audio_attachments(
    attachments: Sequence[Any],
    config: Mapping[str, Any],
    *,
    workspace: Path,
    session_root: Path | None = None,
) -> list[AttachmentCandidate]:
    return [
        candidate
        for index, attachment in enumerate(attachments)
        for candidate in [_candidate_from_attachment(attachment, index, config, workspace=workspace, session_root=session_root)]
        if candidate is not None
    ]


def _candidate_from_attachment(
    attachment: Any,
    index: int,
    config: Mapping[str, Any],
    *,
    workspace: Path,
    session_root: Path | None,
) -> AttachmentCandidate | None:
    if not isinstance(attachment, Mapping):
        return None
    media_type = _first_text(attachment, "media_type", "mime_type", "mime", "content_type")
    filename = _first_text(attachment, "filename", "file_name", "name")
    raw_path = _first_text(attachment, "path", "resolved_path", "local_path", "file_path")
    artifact_id = _first_text(attachment, "artifact_id", "id", "attachment_id")
    identifier = artifact_id or filename or f"attachment[{index}]"
    path = _attachment_path(raw_path, workspace=workspace, session_root=session_root) if raw_path else None
    if path and not filename:
        filename = path.name
    if not media_type and filename:
        media_type = mimetypes.guess_type(filename)[0]
    if not _is_supported_audio(media_type, filename, config):
        return None
    size_bytes = _optional_int(_first_value(attachment, "size_bytes", "size", "byte_size"))
    duration_seconds = _optional_float(_first_value(attachment, "duration_seconds", "duration", "audio_duration"))
    _validate_limits(identifier, size_bytes=size_bytes, duration_seconds=duration_seconds, config=config)
    data = _attachment_bytes(attachment)
    if data is None and path is None:
        raise ValueError(f"audio attachment {identifier} has no host-readable data or path handle")
    return AttachmentCandidate(
        attachment=dict(attachment),
        index=index,
        identifier=identifier,
        filename=filename,
        media_type=media_type,
        path=path,
        data=data,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
    )


def _transcribe_one(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "stt_openai":
        return _transcribe_openai(candidate, config)
    if provider == "stt_groq":
        return _transcribe_openai_compatible(candidate, config, provider_label="Groq STT")
    if provider == "stt_deepgram":
        return _transcribe_deepgram(candidate, config)
    if provider == "stt_assemblyai":
        return _transcribe_assemblyai(candidate, config)
    if provider == "stt_gemini":
        return _transcribe_gemini(candidate, config)
    if provider == "stt_dashscope":
        return _transcribe_dashscope(candidate, config)
    if provider == "stt_baidu":
        return _transcribe_baidu(candidate, config)
    if provider == "stt_tencent":
        return _transcribe_tencent(candidate, config)
    raise ValueError(f"unsupported STT provider: {provider or '(missing)'}")


def _transcribe_openai(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    return _transcribe_openai_compatible(candidate, config, provider_label="OpenAI STT")


def _transcribe_openai_compatible(
    candidate: AttachmentCandidate,
    config: Mapping[str, Any],
    *,
    provider_label: str,
) -> TranscriptionResult:
    api_key = _resolve_secret(config, default_env=str(config.get("api_key_env") or "DEMIURGE_OPENAI_API_KEY"))
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_OPENAI_API_KEY")
        raise ValueError(f"{provider_label} API key is not configured; set {env_name} or config.api_key")
    payload = _drop_none(
        {
            "model": str(config.get("model") or "whisper-1"),
            "language": _optional_str(config.get("language")),
            "prompt": _optional_str(config.get("context_hint") or config.get("prompt")),
            "response_format": str(config.get("response_format") or "json"),
            "temperature": config.get("temperature"),
            "timestamp_granularities[]": _timestamp_granularities(config),
        }
    )
    url = _join_url(str(config.get("base_url") or "https://api.openai.com/v1"), str(config.get("endpoint") or "/audio/transcriptions"))
    raw = _post_multipart(url, candidate, payload, api_key=api_key, provider_label=provider_label, timeout=_timeout(config))
    data = _parse_json(raw, provider_label=provider_label)
    text = str(data.get("text") or "").strip()
    if not text:
        raise RuntimeError(f"{provider_label} returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": str(config.get("provider") or "stt_openai"),
                "model": payload.get("model"),
                "language": data.get("language") or payload.get("language"),
                "duration_seconds": data.get("duration") or candidate.duration_seconds,
                "segments": data.get("segments"),
                "words": data.get("words"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_deepgram(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_DEEPGRAM_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_DEEPGRAM_API_KEY")
        raise ValueError(f"Deepgram STT API key is not configured; set {env_name} or config.api_key")
    language = _optional_str(config.get("language"))
    query = _drop_none(
        {
            "model": str(config.get("model") or "nova-3"),
            "language": language,
            "detect_language": None if language else _config_bool_or_none(config.get("detect_language")),
            "punctuate": _config_bool(config.get("punctuate"), default=True),
            "smart_format": _config_bool(config.get("smart_format"), default=True),
            "diarize": _config_bool_or_none(config.get("diarization")),
            "utterances": _config_bool_or_none(config.get("utterances")),
            "filler_words": _config_bool_or_none(config.get("filler_words")),
            "profanity_filter": _config_bool_or_none(config.get("profanity_filter")),
        }
    )
    if config.get("keywords"):
        query["keywords"] = config.get("keywords")
    url = _url_with_query(str(config.get("base_url") or "https://api.deepgram.com/v1/listen"), query)
    raw = _post_audio_bytes(
        url,
        candidate,
        headers={"Authorization": f"Token {api_key}"},
        provider_label="Deepgram STT",
        timeout=_timeout(config),
    )
    data = _parse_json(raw, provider_label="Deepgram STT")
    channel = _deepgram_channel(data)
    alternatives = _list(channel.get("alternatives"))
    alternative = _mapping(alternatives[0] if alternatives else {})
    text = str(alternative.get("transcript") or "").strip()
    if not text:
        raise RuntimeError("Deepgram STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_deepgram",
                "model": query.get("model"),
                "language": channel.get("detected_language") or query.get("language"),
                "confidence": alternative.get("confidence"),
                "duration_seconds": _mapping(data.get("metadata")).get("duration") or candidate.duration_seconds,
                "words": alternative.get("words"),
                "paragraphs": _mapping(alternative.get("paragraphs")).get("paragraphs"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_assemblyai(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_ASSEMBLYAI_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_ASSEMBLYAI_API_KEY")
        raise ValueError(f"AssemblyAI STT API key is not configured; set {env_name} or config.api_key")
    base_url = str(config.get("base_url") or "https://api.assemblyai.com/v2")
    upload_url = _join_url(base_url, str(config.get("upload_endpoint") or "/upload"))
    transcript_url = _join_url(base_url, str(config.get("transcript_endpoint") or "/transcript"))
    headers = {"Authorization": api_key}
    upload_raw = _post_audio_bytes(upload_url, candidate, headers=headers, provider_label="AssemblyAI upload", timeout=_timeout(config))
    upload = _parse_json(upload_raw, provider_label="AssemblyAI upload")
    audio_url = str(upload.get("upload_url") or "").strip()
    if not audio_url:
        raise RuntimeError("AssemblyAI upload returned no upload_url")
    language = _optional_str(config.get("language"))
    payload = _drop_none(
        {
            "audio_url": audio_url,
            "language_code": language,
            "language_detection": None if language else _config_bool_or_none(config.get("detect_language")),
            "speaker_labels": _config_bool_or_none(config.get("speaker_labels") or config.get("diarization")),
            "punctuate": _config_bool(config.get("punctuate"), default=True),
            "format_text": _config_bool(config.get("format_text"), default=True),
            "filter_profanity": _config_bool_or_none(config.get("profanity_filter")),
            "speech_model": _optional_str(config.get("model")),
        }
    )
    transcript_raw = _post_json(transcript_url, payload, headers=headers, provider_label="AssemblyAI STT", timeout=_timeout(config))
    transcript = _parse_json(transcript_raw, provider_label="AssemblyAI STT")
    result = _poll_assemblyai(transcript_url, str(transcript.get("id") or ""), headers=headers, config=config)
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError("AssemblyAI STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_assemblyai",
                "model": payload.get("speech_model"),
                "language": result.get("language_code") or payload.get("language_code"),
                "confidence": result.get("confidence"),
                "duration_seconds": result.get("audio_duration") or candidate.duration_seconds,
                "utterances": result.get("utterances"),
                "words": result.get("words"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_gemini(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_GEMINI_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_GEMINI_API_KEY")
        raise ValueError(f"Gemini STT API key is not configured; set {env_name} or config.api_key")
    model = str(config.get("model") or "gemini-2.5-flash")
    instruction = str(
        config.get("transcription_instruction")
        or "Transcribe the attached audio. Return only a JSON object with keys text, language, confidence, segments, and warnings."
    )
    language = _optional_str(config.get("language"))
    if language:
        instruction = f"{instruction}\nLanguage hint: {language}."
    if _config_bool(config.get("include_timestamps"), default=False):
        instruction = f"{instruction}\nInclude useful timestamp metadata in segments when possible."

    data = _read_attachment_bytes(candidate)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {
                        "inline_data": {
                            "mime_type": candidate.media_type or "audio/mpeg",
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    if config.get("temperature") is not None:
        payload["generationConfig"]["temperature"] = config.get("temperature")
    url = _gemini_generate_url(str(config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta"), model)
    raw = _post_json(
        url,
        payload,
        headers={"x-goog-api-key": api_key},
        provider_label="Gemini STT",
        timeout=_timeout(config),
    )
    response = _parse_json(raw, provider_label="Gemini STT")
    text_payload = _gemini_text(response)
    parsed = _parse_json_text(text_payload)
    text = str(parsed.get("text") or text_payload or "").strip()
    if not text:
        raise RuntimeError("Gemini STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_gemini",
                "model": model,
                "language": parsed.get("language"),
                "confidence": parsed.get("confidence"),
                "segments": parsed.get("segments"),
                "warnings": parsed.get("warnings"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_dashscope(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    api_key = _resolve_secret(config, default_env="DEMIURGE_DASHSCOPE_API_KEY")
    if not api_key:
        env_name = str(config.get("api_key_env") or "DEMIURGE_DASHSCOPE_API_KEY")
        raise ValueError(f"DashScope STT API key is not configured; set {env_name} or config.api_key")
    model = str(config.get("model") or "qwen3-asr-flash")
    instruction = str(config.get("transcription_instruction") or "Transcribe the attached audio into text. Return only the transcript.")
    language = _optional_str(config.get("language"))
    if language:
        instruction = f"{instruction}\nLanguage hint: {language}."
    data = _read_attachment_bytes(candidate)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": _audio_data_url(data, candidate, config),
                        },
                    },
                ],
            }
        ],
        "stream": False,
    }
    asr_options = _drop_none(
        {
            "language": _optional_str(config.get("language")),
            "enable_itn": _config_bool_or_none(config.get("enable_itn")),
        }
    )
    if asr_options:
        payload["asr_options"] = asr_options
    if config.get("temperature") is not None:
        payload["temperature"] = config.get("temperature")
    url = _join_url(str(config.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1"), str(config.get("endpoint") or "/chat/completions"))
    raw = _post_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
        provider_label="DashScope STT",
        timeout=_timeout(config),
    )
    response = _parse_json(raw, provider_label="DashScope STT")
    text_payload = _chat_completion_text(response)
    parsed = _parse_json_text(text_payload)
    text = str(parsed.get("text") or parsed.get("transcript") or text_payload or "").strip()
    if not text:
        raise RuntimeError("DashScope STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_dashscope",
                "model": model,
                "language": parsed.get("language") or language,
                "confidence": parsed.get("confidence"),
                "segments": parsed.get("segments"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_baidu(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    access_token = _baidu_access_token(config)
    audio = _read_attachment_bytes(candidate)
    payload = _drop_none(
        {
            "format": _infer_audio_format(candidate, config),
            "rate": _positive_int(config.get("sample_rate"), default=16000),
            "channel": _positive_int(config.get("channel"), default=1),
            "cuid": str(config.get("cuid") or "demiurge"),
            "token": access_token or None,
            "dev_pid": _positive_int(config.get("dev_pid"), default=1537),
            "speech": base64.b64encode(audio).decode("ascii"),
            "len": len(audio),
        }
    )
    raw = _post_json(
        str(config.get("base_url") or "https://vop.baidu.com/server_api"),
        payload,
        headers={},
        provider_label="Baidu STT",
        timeout=_timeout(config),
    )
    response = _parse_json(raw, provider_label="Baidu STT")
    err_no = response.get("err_no")
    if err_no not in (0, "0", None):
        raise RuntimeError(f"Baidu STT failed: {response.get('err_msg') or response.get('error_msg') or err_no}")
    result = _list(response.get("result"))
    text = "\n".join(str(item).strip() for item in result if str(item).strip()).strip()
    if not text:
        text = str(response.get("text") or response.get("transcript") or "").strip()
    if not text:
        raise RuntimeError("Baidu STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_baidu",
                "model": str(config.get("model") or "极速版语音识别"),
                "language": config.get("language"),
                "duration_seconds": candidate.duration_seconds,
                "corpus_no": response.get("corpus_no"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _transcribe_tencent(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> TranscriptionResult:
    secret_id = _resolve_secret_key(config, key="secret_id", env_key="secret_id_env", default_env="DEMIURGE_TENCENT_SECRET_ID")
    secret_key = _resolve_secret_key(config, key="secret_key", env_key="secret_key_env", default_env="DEMIURGE_TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        raise ValueError("Tencent STT credentials are not configured; set DEMIURGE_TENCENT_SECRET_ID and DEMIURGE_TENCENT_SECRET_KEY")
    audio = _read_attachment_bytes(candidate)
    service = "asr"
    host = str(config.get("host") or "asr.tencentcloudapi.com")
    action = str(config.get("action") or "SentenceRecognition")
    version = str(config.get("version") or "2019-06-14")
    region = str(config.get("region") or "ap-shanghai")
    payload = _drop_none(
        {
            "ProjectId": _positive_int(config.get("project_id"), default=0),
            "SubServiceType": _positive_int(config.get("sub_service_type"), default=2),
            "EngSerViceType": str(config.get("engine_model_type") or config.get("model") or "16k_zh"),
            "SourceType": 1,
            "VoiceFormat": _infer_audio_format(candidate, config),
            "UsrAudioKey": candidate.identifier,
            "Data": base64.b64encode(audio).decode("ascii"),
            "DataLen": len(audio),
            "WordInfo": _positive_int(config.get("word_info"), default=0),
            "FilterDirty": _positive_int(config.get("filter_dirty"), default=0),
            "FilterModal": _positive_int(config.get("filter_modal"), default=0),
            "ConvertNumMode": _positive_int(config.get("convert_num_mode"), default=1),
            "HotwordId": _optional_str(config.get("hotword_id")),
            "CustomizationId": _optional_str(config.get("customization_id")),
        }
    )
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    headers = _tencent_headers(
        body,
        secret_id=secret_id,
        secret_key=secret_key,
        service=service,
        host=host,
        action=action,
        version=version,
        region=region,
        timestamp=timestamp,
    )
    request = urllib.request.Request(f"https://{host}", data=body, method="POST", headers=headers)
    raw = _urlopen_read(request, provider_label="Tencent STT", timeout=_timeout(config))
    response = _parse_json(raw, provider_label="Tencent STT")
    response_body = _mapping(response.get("Response"))
    error = _mapping(response_body.get("Error"))
    if error:
        raise RuntimeError(f"Tencent STT failed: {error.get('Code') or 'Error'} {error.get('Message') or ''}".strip())
    text = str(response_body.get("Result") or "").strip()
    if not text:
        raise RuntimeError("Tencent STT returned an empty transcript")
    return TranscriptionResult(
        text=text,
        metadata=_drop_none(
            {
                "provider": "stt_tencent",
                "model": payload.get("EngSerViceType"),
                "duration_seconds": candidate.duration_seconds,
                "request_id": response_body.get("RequestId"),
                "source": _source_metadata(candidate),
            }
        ),
    )


def _post_multipart(
    url: str,
    candidate: AttachmentCandidate,
    fields: Mapping[str, Any],
    *,
    api_key: str,
    provider_label: str,
    timeout: float,
) -> bytes:
    boundary = "demiurge-stt-boundary"
    body = bytearray()
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None:
                continue
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(item).encode("utf-8"))
            body.extend(b"\r\n")
    filename = candidate.filename or "audio"
    media_type = candidate.media_type or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {media_type}\r\n\r\n".encode("utf-8"))
    body.extend(_read_attachment_bytes(candidate))
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return _urlopen_read(request, provider_label=provider_label, timeout=timeout)


def _post_audio_bytes(
    url: str,
    candidate: AttachmentCandidate,
    *,
    headers: Mapping[str, str],
    provider_label: str,
    timeout: float,
) -> bytes:
    request_headers = {"Content-Type": candidate.media_type or "application/octet-stream", **dict(headers)}
    request = urllib.request.Request(url, data=_read_attachment_bytes(candidate), method="POST", headers=request_headers)
    return _urlopen_read(request, provider_label=provider_label, timeout=timeout)


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str],
    provider_label: str,
    timeout: float,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **dict(headers)},
    )
    return _urlopen_read(request, provider_label=provider_label, timeout=timeout)


def _urlopen_read(request: urllib.request.Request, *, provider_label: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = _safe_error_body(exc)
        raise RuntimeError(f"{provider_label} HTTP error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{provider_label} request failed: {exc.reason}") from exc


def _poll_assemblyai(
    transcript_url: str,
    transcript_id: str,
    *,
    headers: Mapping[str, str],
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not transcript_id:
        raise RuntimeError("AssemblyAI STT returned no transcript id")
    max_polls = _positive_int(config.get("max_polls"), default=30)
    poll_interval = _positive_float(config.get("poll_interval_seconds"), default=3.0)
    timeout = _timeout(config)
    url = _join_url(transcript_url, transcript_id)
    for attempt in range(max_polls):
        if attempt:
            import time

            time.sleep(poll_interval)
        request = urllib.request.Request(url, method="GET", headers=dict(headers))
        data = _parse_json(_urlopen_read(request, provider_label="AssemblyAI STT", timeout=timeout), provider_label="AssemblyAI STT")
        status = str(data.get("status") or "").lower()
        if status == "completed":
            return data
        if status == "error":
            raise RuntimeError(f"AssemblyAI STT failed: {data.get('error') or 'unknown error'}")
    raise TimeoutError("AssemblyAI STT did not complete before max_polls")


def _read_attachment_bytes(candidate: AttachmentCandidate) -> bytes:
    if candidate.data is not None:
        return candidate.data
    if candidate.path is None:
        raise ValueError(f"audio attachment {candidate.identifier} has no readable data")
    return candidate.path.read_bytes()


def _attachment_bytes(attachment: Mapping[str, Any]) -> bytes | None:
    for key in ("bytes", "data", "content"):
        value = attachment.get(key)
        if isinstance(value, bytes):
            return value
    for key in ("base64", "data_base64", "content_base64"):
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            try:
                return base64.b64decode(value, validate=True)
            except ValueError as exc:
                raise ValueError(f"attachment {key} is not valid base64") from exc
    return None


def _attachment_path(raw_path: str, *, workspace: Path, session_root: Path | None) -> Path:
    workspace = workspace.resolve()
    session_root = session_root.resolve() if session_root is not None else None
    allowed_roots = [root for root in (session_root, workspace) if root is not None]
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        candidates = [path.resolve()]
    else:
        candidates = [(root / path).resolve() for root in allowed_roots]
    for resolved in candidates:
        if any(_is_relative_to(resolved, root) for root in allowed_roots) and resolved.exists():
            return resolved
    resolved = candidates[0]
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise ValueError("audio attachment path must resolve inside the workspace or session root")
    return resolved


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_supported_audio(media_type: str | None, filename: str | None, config: Mapping[str, Any]) -> bool:
    allowed_mime = {str(item).strip().lower() for item in _list(config.get("allowed_mime_types")) if str(item).strip()}
    allowed_ext = {str(item).strip().lower() for item in _list(config.get("allowed_extensions")) if str(item).strip()}
    if not allowed_mime:
        allowed_mime = set(DEFAULT_SUPPORTED_MIME_TYPES)
    if not allowed_ext:
        allowed_ext = set(DEFAULT_SUPPORTED_EXTENSIONS)
    normalized_mime = str(media_type or "").split(";", 1)[0].strip().lower()
    if normalized_mime and (normalized_mime in allowed_mime or normalized_mime.startswith(AUDIO_MIME_PREFIX)):
        return True
    suffix = Path(filename or "").suffix.lower()
    return bool(suffix and suffix in allowed_ext)


def _validate_limits(
    identifier: str,
    *,
    size_bytes: int | None,
    duration_seconds: float | None,
    config: Mapping[str, Any],
) -> None:
    max_mb = _positive_float(config.get("max_audio_mb"), default=25.0)
    if size_bytes is not None and size_bytes > max_mb * 1024 * 1024:
        raise ValueError(f"audio attachment {identifier} is larger than {max_mb:g} MB")
    max_seconds = _positive_float(config.get("max_audio_seconds"), default=1800.0)
    if duration_seconds is not None and duration_seconds > max_seconds:
        raise ValueError(f"audio attachment {identifier} is longer than {max_seconds:g} seconds")


def _no_audio_message(attachments: Sequence[Any], config: Mapping[str, Any]) -> str:
    if attachments:
        return "no supported audio attachment found for transcription"
    required = str(config.get("required_message") or "Send a voice or audio attachment to transcribe.")
    return required


def _timestamp_granularities(config: Mapping[str, Any]) -> list[str] | None:
    value = str(config.get("timestamp_granularity") or "none").strip().lower()
    if value in {"", "none", "false"}:
        return None
    if value == "word":
        return ["word"]
    if value == "segment":
        return ["segment"]
    if value in {"word,segment", "segment,word", "both"}:
        return ["word", "segment"]
    return [value]


def _deepgram_channel(data: Mapping[str, Any]) -> Mapping[str, Any]:
    results = _mapping(data.get("results"))
    channels = _list(results.get("channels"))
    return _mapping(channels[0] if channels else {})


def _gemini_generate_url(base_url: str, model: str) -> str:
    base = base_url.rstrip("/")
    quoted_model = urllib.parse.quote(model, safe=".-_")
    return f"{base}/models/{quoted_model}:generateContent"


def _gemini_text(response: Mapping[str, Any]) -> str:
    for candidate in _list(response.get("candidates")):
        content = _mapping(candidate.get("content"))
        parts = _list(content.get("parts"))
        text = "".join(str(_mapping(part).get("text") or "") for part in parts).strip()
        if text:
            return text
    return ""


def _chat_completion_text(response: Mapping[str, Any]) -> str:
    choices = _list(response.get("choices"))
    if not choices:
        return ""
    message = _mapping(_mapping(choices[0]).get("message"))
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(str(_mapping(item).get("text") or "") for item in content).strip()
    return ""


def _parse_json_text(value: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"text": value}
    return parsed if isinstance(parsed, Mapping) else {"text": value}


def _parse_json(raw: bytes, *, provider_label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider_label} returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{provider_label} returned a non-object JSON response")
    return parsed


def _url_with_query(base_url: str, query: Mapping[str, Any]) -> str:
    flat: list[tuple[str, str]] = []
    for key, value in query.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None:
                continue
            if isinstance(item, bool):
                item = str(item).lower()
            flat.append((str(key), str(item)))
    if not flat:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urllib.parse.urlencode(flat)}"


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


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


def _resolve_secret_key(config: Mapping[str, Any], *, key: str, env_key: str, default_env: str) -> str:
    direct = str(config.get(key) or "").strip()
    if direct:
        return direct
    env_names = [str(config.get(env_key) or default_env).strip()]
    env_names.extend(str(item).strip() for item in _list(config.get(f"{key}_fallback_envs")))
    for env_name in env_names:
        if not env_name:
            continue
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def _baidu_access_token(config: Mapping[str, Any]) -> str:
    direct = _resolve_secret_key(
        config,
        key="access_token",
        env_key="access_token_env",
        default_env="DEMIURGE_BAIDU_ACCESS_TOKEN",
    )
    if direct:
        return direct
    api_key = _resolve_secret(config, default_env="DEMIURGE_BAIDU_API_KEY")
    secret_key = _resolve_secret_key(
        config,
        key="secret_key",
        env_key="secret_key_env",
        default_env="DEMIURGE_BAIDU_SECRET_KEY",
    )
    if not api_key or not secret_key:
        raise ValueError(
            "Baidu STT credentials are not configured; set DEMIURGE_BAIDU_ACCESS_TOKEN "
            "or both DEMIURGE_BAIDU_API_KEY and DEMIURGE_BAIDU_SECRET_KEY"
        )
    token_url = _url_with_query(
        str(config.get("token_url") or "https://aip.baidubce.com/oauth/2.0/token"),
        {
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        },
    )
    request = urllib.request.Request(token_url, method="POST")
    response = _parse_json(_urlopen_read(request, provider_label="Baidu OAuth", timeout=_timeout(config)), provider_label="Baidu OAuth")
    token = str(response.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"Baidu OAuth returned no access_token: {response.get('error_description') or response.get('error') or 'unknown error'}")
    return token


def _infer_audio_format(candidate: AttachmentCandidate, config: Mapping[str, Any]) -> str:
    configured = str(config.get("audio_format") or "").strip().lower()
    if configured:
        return configured
    suffix = Path(candidate.filename or "").suffix.lower().lstrip(".")
    if suffix == "mpga":
        return "mp3"
    if suffix:
        return suffix
    media = str(candidate.media_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "audio/mp4": "mp4",
        "audio/aac": "aac",
        "audio/ogg": "ogg",
        "audio/webm": "webm",
        "audio/flac": "flac",
        "audio/pcm": "pcm",
        "audio/amr": "amr",
    }.get(media, "mp3")


def _audio_data_url(data: bytes, candidate: AttachmentCandidate, config: Mapping[str, Any]) -> str:
    media_type = candidate.media_type or _mime_for_audio_format(_infer_audio_format(candidate, config))
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _mime_for_audio_format(audio_format: str) -> str:
    return {
        "aac": "audio/aac",
        "flac": "audio/flac",
        "m4a": "audio/m4a",
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "pcm": "audio/pcm",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "amr": "audio/amr",
    }.get(audio_format, "audio/mpeg")


def _tencent_headers(
    body: bytes,
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    action: str,
    version: str,
    region: str,
    timestamp: int,
) -> dict[str, str]:
    algorithm = "TC3-HMAC-SHA256"
    date = _datetime.datetime.fromtimestamp(timestamp, _datetime.timezone.utc).strftime("%Y-%m-%d")
    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashlib.sha256(body).hexdigest(),
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": region,
    }


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _source_metadata(candidate: AttachmentCandidate) -> dict[str, Any]:
    return _drop_none(
        {
            "id": candidate.identifier,
            "filename": candidate.filename,
            "media_type": candidate.media_type,
            "size_bytes": candidate.size_bytes,
            "duration_seconds": candidate.duration_seconds,
            "index": candidate.index,
        }
    )


def _first_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return None


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str | None:
    value = _first_value(mapping, *keys)
    text = str(value or "").strip()
    return text or None


def _config_bool(value: Any, *, default: bool) -> bool:
    parsed = _config_bool_or_none(value)
    return default if parsed is None else parsed


def _config_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, *, default: int) -> int:
    parsed = _optional_int(value)
    return parsed if parsed and parsed > 0 else default


def _positive_float(value: Any, *, default: float) -> float:
    parsed = _optional_float(value)
    return parsed if parsed and parsed > 0 else default


def _timeout(config: Mapping[str, Any]) -> float:
    return _positive_float(config.get("timeout_seconds"), default=60.0)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


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
