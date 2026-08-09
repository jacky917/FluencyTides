"""
STT (Speech-to-Text) 基礎設施套件。

STT (speech-to-text) infrastructure package.

封裝自架 Whisper (Speaches) 服務的轉錄客戶端，供 stt_diff 與
stt_llm 兩種語音評分模式共用。

Wraps the transcription client for the self-hosted Whisper (Speaches)
service, shared by the stt_diff and stt_llm audio-evaluation modes.
"""

from app.infrastructure.stt.whisper_client import WhisperClient

__all__ = ["WhisperClient"]
