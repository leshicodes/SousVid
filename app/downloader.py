"""
downloader.py -- Downloads video from any yt-dlp-supported URL.

Supported platforms include Instagram Reels, TikTok, YouTube Shorts, and
1,000+ others. Instagram requires browser cookies; see .env.example for details.
"""
import logging
import os
import tempfile
from pathlib import Path

import yt_dlp

from app.config import settings
from app.errors import DownloadError

logger = logging.getLogger(__name__)


def download_video(url: str) -> tuple[str, str | None]:
    """
    Download a video and return its local path alongside the post description.

    The description is the video caption or post text as provided by the
    platform -- many creators paste their full recipe there, which lets the
    pipeline skip Whisper entirely.

    The caller is responsible for cleaning up the returned file's parent
    directory when done.

    Raises:
        DownloadError: if yt-dlp cannot download the video.
    """
    tmp_dir = tempfile.mkdtemp(prefix="sousvid_dl_")
    output_template = os.path.join(tmp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        # Prefer mp4 for ffmpeg compatibility; fall back to best available format.
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # Polite rate-limiting -- important for Instagram, which rate-limits aggressively.
        "sleep_interval": 2,
        "max_sleep_interval": 5,
        "retries": 3,
        "fragment_retries": 3,
    }

    if os.path.isfile(settings.cookies_file):
        ydl_opts["cookiefile"] = settings.cookies_file
        logger.info(f"Using cookies from: {settings.cookies_file}")
    else:
        logger.warning(
            f"No cookies file found at {settings.cookies_file}. "
            "Instagram Reels will likely fail -- see .env.example for setup instructions."
        )

    logger.info(f"Downloading: {url}")
    description: str | None = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info:
                description = info.get("description")
    except yt_dlp.utils.DownloadError as exc:
        err = str(exc)
        if "instagram" in err.lower() or "Unable to extract" in err:
            raise DownloadError(
                "Could not download this Instagram Reel. This almost always means "
                "missing or expired browser cookies. Export fresh cookies from "
                "instagram.com using the 'Get cookies.txt LOCALLY' browser extension, "
                "save to cookies/cookies.txt, and restart the container."
            ) from exc
        raise DownloadError(str(exc)) from exc

    files = list(Path(tmp_dir).iterdir())
    if not files:
        raise DownloadError(f"yt-dlp produced no output file in {tmp_dir}")

    video_path = str(files[0])
    logger.info(f"Downloaded to: {video_path}")
    return video_path, description
