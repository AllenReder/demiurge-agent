---
name: stt_transcription
description: Interpret speech-to-text transcripts that were added to the prompt from voice or audio attachments.
category: audio
---

# Speech-to-Text Transcription

The current user prompt may include a `Voice message transcript` block produced from an audio attachment before the model request.

Use the transcript as the user's spoken input. If the original text prompt also contains instructions, combine it with the transcript instead of treating the transcript as a separate user.

When transcript metadata mentions low confidence, missing language, warnings, or partial provider output, mention uncertainty and ask a clarifying question rather than inventing missing words. Do not claim speaker identity unless speaker labels or diarization metadata is present.
