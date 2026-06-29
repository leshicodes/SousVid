"""
errors.py -- Application exception hierarchy for SousVid.

Raising typed exceptions from business logic lets the API layer translate them
into user-facing messages without leaking internal details.
"""


class SousVidError(Exception):
    pass


class DownloadError(SousVidError):
    """Raised when video download fails (yt-dlp or file system)."""


class TranscriptionError(SousVidError):
    """Raised when audio transcription fails (Whisper)."""


class ExtractionError(SousVidError):
    """Raised when the LLM fails to return a parseable recipe."""
