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


def extract_frames(
    video_path: str,
    num_frames: int | None = None,
    offset: float = 0.5,
    segment: str = "all",
) -> list[str]:
    """
    Extract evenly-spaced frames from a video and return them as base64 JPEGs.

    Timestamps are spread across the video length, avoiding the very first and
    last frames (which are often title cards or static end screens).

    Args:
        video_path: Absolute path to the video file.
        num_frames:  How many frames to extract. Defaults to settings.max_frames.
        offset:      A float between 0.0 and 1.0 to offset the frame timestamps (used for "all" segment).
        segment:     The segment strategy ("all" or "hook_and_plating").

    Returns:
        List of base64-encoded JPEG strings, ready to attach to an LLM message.
    """
    count = num_frames if num_frames is not None else settings.max_frames
    duration = _video_duration(video_path)
    tmp_dir = tempfile.mkdtemp(prefix="sousvid_frames_")
    frames_b64: list[str] = []

    # Calculate timestamps based on segment strategy
    timestamps: list[float] = []
    if segment == "hook_and_plating":
        # Split count: half in first 25% (hook), half in last 25% (plating/eating)
        first_count = count // 2
        last_count = count - first_count

        # First 25% of the video
        first_duration = duration * 0.25
        for i in range(first_count):
            timestamps.append(first_duration * (i + 0.5) / first_count)

        # Last 25% of the video
        last_start = duration * 0.75
        last_duration = duration * 0.25
        for i in range(last_count):
            timestamps.append(last_start + last_duration * (i + 0.5) / last_count)
    else:
        # Standard: even spacing across the entire duration (with offset)
        for i in range(count):
            timestamps.append(duration * (i + offset) / count)

    try:
        for i, timestamp in enumerate(timestamps):
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
