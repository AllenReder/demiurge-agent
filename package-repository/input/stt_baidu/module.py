from .stt_common.input import build_process
from .stt_baidu.transcriber import audio_attachments, load_transcription_config, transcribe_attachments

process = build_process(__file__, load_transcription_config, audio_attachments, transcribe_attachments)
