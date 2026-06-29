"""
transcriber.py -- Transcribes video audio using faster-whisper.

faster-whisper uses CTranslate2 under the hood, which means no PyTorch
dependency and good CPU performance even on modest hardware.

The WhisperModel is loaded once at startup (or on first use) and reused for
every request. Loading takes a few seconds; re-loading per-request would be
prohibitively slow.
"""
import logging

from faster_whisper import WhisperModel

from app.config import settings

logger = logging.getLogger(__name__)

# Silence duration below which Whisper ignores a gap in speech.
VAD_MIN_SILENCE_MS = 500

# Module-level singleton -- initialized once, shared across all requests.
_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    """
    Return the loaded Whisper model, initializing it on first call.

    Thread-safe in practice because we run with a single-worker executor,
    but the guard is explicit for clarity.
    """
    global _model
    if _model is None:
        logger.info(
            f"Loading Whisper '{settings.whisper_model}' on "
            f"{settings.whisper_device} ({settings.whisper_compute_type})..."
        )
        _model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        logger.info("Whisper model ready.")
    return _model


def transcribe(video_path: str) -> str:
    """
    Transcribe the audio track of a video and return the full text.

    Voice activity detection (VAD) is enabled to skip silent sections,
    which significantly reduces hallucinations in videos with music or
    background noise.
    """
    model = get_model()
    logger.info(f"Transcribing: {video_path}")

    segments, info = model.transcribe(
        video_path,
        beam_size=5,
        language=None,   # auto-detect; works well across English, Italian, Spanish, etc.
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": VAD_MIN_SILENCE_MS},
    )

    transcript = " ".join(segment.text.strip() for segment in segments)
    logger.info(
        f"Transcription complete. Language: {info.language} ({len(transcript)} chars)"
    )
    return transcript.strip()
