"""
audio.py — SoundCloud audio search and download via yt-dlp.
"""

import logging
import os
import tempfile

import yt_dlp

logger = logging.getLogger(__name__)


def search_and_download_soundcloud(query: str, output_dir: str | None = None) -> dict:
    """
    Search SoundCloud for `query` and download the best-quality audio.

    Returns a dict:
        {
            "file":     "/path/to/audio.mp3",
            "title":    "Track Title",
            "artist":   "Artist Name",
            "duration": 210.5,          # seconds (float), may be None
            "url":      "https://soundcloud.com/...",
        }

    Raises RuntimeError if nothing is found or download fails.
    """
    if output_dir is None:
        output_dir = tempfile.gettempdir()

    # yt-dlp writes the final file as <id>.<ext>; we capture the real path via hook.
    downloaded_path: list[str] = []

    def progress_hook(d):
        if d["status"] == "finished":
            downloaded_path.append(d["filename"])

    ydl_opts = {
        # scsearch1: fetches the single top SoundCloud result
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "progress_hooks": [progress_hook],
        # Keep the ffmpeg-converted file path consistent
        "postprocessor_args": [],
        "keepvideo": False,
    }

    search_url = f"scsearch1:{query}"
    logger.info("SoundCloud search: %r", query)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_url, download=True)

    # Unwrap playlist wrapper if present
    if info and "entries" in info:
        info = info["entries"][0] if info["entries"] else None

    if not info:
        raise RuntimeError(f"No SoundCloud results for query: {query!r}")

    # yt-dlp postprocessors rename the file to .mp3
    track_id = info.get("id", "unknown")
    expected_mp3 = os.path.join(output_dir, f"{track_id}.mp3")

    # Fall back to whatever the progress hook captured
    if not os.path.exists(expected_mp3) and downloaded_path:
        base, _ = os.path.splitext(downloaded_path[0])
        expected_mp3 = base + ".mp3"

    if not os.path.exists(expected_mp3):
        # Last-ditch: search directory for a recently modified mp3
        candidates = [
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.endswith(".mp3")
        ]
        if not candidates:
            raise RuntimeError("Audio download succeeded but output file not found.")
        expected_mp3 = max(candidates, key=os.path.getmtime)

    logger.info("Downloaded audio: %s", expected_mp3)

    return {
        "file": expected_mp3,
        "title": info.get("title") or "",
        "artist": info.get("uploader") or info.get("artist") or "",
        "duration": info.get("duration"),
        "url": info.get("webpage_url") or "",
    }
