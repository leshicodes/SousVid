"""
frame_extractor.py -- Extracts evenly-spaced frames from a video for LLM input.

Frames are taken from ffmpeg via one subprocess call per frame. A single
multi-output ffmpeg command would be faster, but the per-frame approach keeps
error handling simple and the performance difference is negligible for short
social-media clips.
"""
import base64
import logging
import os
import shutil
import subprocess
import tempfile

from app.config import settings

logger = logging.getLogger(__name__)

# ffmpeg encoding parameters -- adjust if you need higher quality or smaller tokens
JPEG_QUALITY = 3      # 1 (best) to 31 (worst); 3 is visually lossless for LLM purposes
FRAME_WIDTH = 640     # pixels; height is calculated automatically to preserve aspect ratio


def _video_duration(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def extract_frames(video_path: str, num_frames: int | None = None) -> list[str]:
    """
    Extract evenly-spaced frames from a video and return them as base64 JPEGs.

    Timestamps are spread across the video length, avoiding the very first and
    last frames (which are often title cards or static end screens).

    Args:
        video_path: Absolute path to the video file.
        num_frames:  How many frames to extract. Defaults to settings.max_frames.

    Returns:
        List of base64-encoded JPEG strings, ready to attach to an LLM message.
    """
    count = num_frames if num_frames is not None else settings.max_frames
    duration = _video_duration(video_path)
    tmp_dir = tempfile.mkdtemp(prefix="sousvid_frames_")
    frames_b64: list[str] = []

    try:
        for i in range(count):
            # Place timestamps evenly across the video, offset by half-step so we
            # don't land exactly on the first or last frame.
            timestamp = duration * (i + 0.5) / count
            frame_path = os.path.join(tmp_dir, f"frame_{i:02d}.jpg")

            cmd = [
                "ffmpeg",
                "-ss", str(timestamp),
                "-i", video_path,
                "-vframes", "1",
                "-q:v", str(JPEG_QUALITY),
                "-vf", f"scale={FRAME_WIDTH}:-2",
                frame_path,
                "-y",
                "-loglevel", "error",
            ]
            subprocess.run(cmd, check=True)

            if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                with open(frame_path, "rb") as f:
                    frames_b64.append(base64.b64encode(f.read()).decode("utf-8"))
            else:
                logger.warning(f"Frame {i} at t={timestamp:.1f}s was empty -- skipping.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    logger.info(f"Extracted {len(frames_b64)} frames from {duration:.1f}s video.")
    return frames_b64
