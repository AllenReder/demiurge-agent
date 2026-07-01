from __future__ import annotations

from pathlib import Path

from ..stt_common import transcriber as _common
from ..stt_common.transcriber import TranscriptionResult, audio_attachments, transcribe_attachments


def load_transcription_config(slot_file: str | Path | None = None) -> dict[str, object]:
    config = _common._load_yaml_mapping(Path(__file__).with_name("config.yaml"))
    if slot_file is None:
        return config
    return _common._merge_config(config, _common._load_yaml_mapping(Path(slot_file).with_name("config.yaml")))
