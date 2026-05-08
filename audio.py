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

    Two-pass approach:
      1. Search via `scsearch1:` with download=False to resolve the real
         webpage URL.  yt-dlp's SoundCloud search can return internal
         api.soundcloud.com/tracks/… URLs that its own extractor then
         refuses to download — fetching the webpage_url first avoids that.
      2. Download from the resolved webpage URL.

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

    base_opts = {"quiet": True, "no_warnings": True}

    # ── Pass 1: search only, no download ────────────────────────────────────
    logger.info("SoundCloud search (pass 1): %r", query)
    with yt_dlp.YoutubeDL({**base_opts, "extract_flat": False}) as ydl:
        info = ydl.extract_info(f"scsearch1:{query}", download=False)

    if info and "entries" in info:
        info = info["entries"][0] if info["entries"] else None

    if not info:
        raise RuntimeError(f"No SoundCloud results for query: {query!r}")

    # Prefer the human-readable webpage URL; fall back to whatever we have.
    track_url = info.get("webpage_url") or info.get("url") or ""
    if not track_url or "api.soundcloud.com" in track_url:
        # Try to reconstruct from the permalink fields
        permalink = info.get("permalink_url") or ""
        uploader_url = info.get("uploader_url") or ""
        slug = info.get("webpage_url_basename") or info.get("id", "")
        if permalink:
            track_url = permalink
        elif uploader_url and slug:
            track_url = f"{uploader_url.rstrip('/')}/{slug}"
        else:
            # Last resort: ask yt-dlp to search again and grab the first
            # result's real URL via the SoundCloud web search page.
            track_url = f"scsearch1:{query}"

    logger.info("Resolved track URL: %s", track_url)

    # ── Pass 2: download from the resolved URL ───────────────────────────────
    downloaded_path: list[str] = []

    def progress_hook(d):
        if d["status"] == "finished":
            downloaded_path.append(d["filename"])

    dl_opts = {
        **base_opts,
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
        "keepvideo": False,
    }

    logger.info("SoundCloud download (pass 2): %s", track_url)
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        dl_info = ydl.extract_info(track_url, download=True)

    if dl_info and "entries" in dl_info:
        dl_info = dl_info["entries"][0] if dl_info["entries"] else dl_info

    # ── Locate the output file ───────────────────────────────────────────────
    track_id = (dl_info or info).get("id", "unknown")
    expected_mp3 = os.path.join(output_dir, f"{track_id}.mp3")

    if not os.path.exists(expected_mp3) and downloaded_path:
        base, _ = os.path.splitext(downloaded_path[0])
        expected_mp3 = base + ".mp3"

    if not os.path.exists(expected_mp3):
        candidates = sorted(
            [os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".mp3")],
            key=os.path.getmtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError("Audio download succeeded but output .mp3 file not found.")
        expected_mp3 = candidates[0]

    logger.info("Downloaded audio: %s", expected_mp3)

    merged = {**(info or {}), **(dl_info or {})}
    return {
        "file": expected_mp3,
        "title": merged.get("title") or "",
        "artist": merged.get("uploader") or merged.get("artist") or "",
        "duration": merged.get("duration"),
        "url": merged.get("webpage_url") or track_url,
    }
