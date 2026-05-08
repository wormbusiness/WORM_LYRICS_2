"""
lyrics.py — lrclib.net search + LRC / plain-text parsing helpers.
"""

import re
import logging
from typing import List, Tuple

import requests

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
TIMEOUT = 15  # seconds


# ── API ──────────────────────────────────────────────────────────────────────────

def search_lyrics(query: str) -> list:
    """
    Search lrclib.net with a free-text query.
    Returns a list of track dicts (may be empty).
    """
    try:
        resp = requests.get(
            f"{LRCLIB_BASE}/search",
            params={"q": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("lrclib search failed: %s", exc)
        return []


def get_lyrics_by_id(lrclib_id: int) -> dict:
    """Fetch a single track's full data by lrclib ID."""
    resp = requests.get(f"{LRCLIB_BASE}/get/{lrclib_id}", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── LRC parsing ──────────────────────────────────────────────────────────────────

# Matches [MM:SS.xx] or [MM:SS] timestamps (enhanced LRC / simple LRC)
_LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
# Metadata tags like [ar:Artist] — we skip these
_LRC_META = re.compile(r"\[[a-z]+:.*?\]", re.IGNORECASE)


def parse_lrc(lrc_text: str) -> List[Tuple[float, str]]:
    """
    Parse LRC-formatted lyrics into a sorted list of (timestamp_seconds, text) tuples.
    Empty/whitespace-only lines and metadata tags are discarded.
    """
    results: List[Tuple[float, str]] = []

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line or _LRC_META.fullmatch(line):
            continue

        match = _LRC_LINE.match(line)
        if not match:
            continue

        minutes, seconds, text = match.groups()
        text = text.strip()
        if not text:
            continue

        ts = int(minutes) * 60.0 + float(seconds)
        results.append((ts, text))

    results.sort(key=lambda x: x[0])
    return results


def distribute_plain_lyrics(
    plain_text: str,
    start_sec: float,
    end_sec: float,
) -> List[Tuple[float, str]]:
    """
    For tracks that only have plain (un-timed) lyrics, distribute lines evenly
    across the [start_sec, end_sec] window so the video renderer still works.
    """
    lines = [l.strip() for l in plain_text.splitlines() if l.strip()]
    if not lines:
        return []

    duration = end_sec - start_sec
    interval = duration / len(lines)
    return [(start_sec + i * interval, line) for i, line in enumerate(lines)]
