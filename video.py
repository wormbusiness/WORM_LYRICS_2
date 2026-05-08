"""
video.py — Lyric video renderer.

Produces a 720×720 MP4 where:
  • The active lyric line is shown bright / highlighted in the centre.
  • The previous and next lines are shown dimmed above and below.
  • A thin progress bar runs across the bottom.
  • Artist + song title appear in a small header.
  • Text wraps automatically and supports any Unicode / CJK script via
    the Noto Sans family installed in the Docker image.
"""

import logging
import os
import re
from typing import List, Optional, Tuple

import numpy as np
from moviepy.editor import AudioFileClip, VideoClip
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────────

VIDEO_SIZE      = (720, 720)
FPS             = 24
BG_COLOR        = (10, 10, 15)          # near-black
HIGHLIGHT_COLOR = (255, 215, 60)        # gold — active line
CURRENT_COLOR   = (240, 240, 255)       # almost-white — same line fallback
DIM_COLOR       = (85, 85, 100)         # muted grey — prev / next lines
TITLE_COLOR     = (130, 130, 145)       # header metadata
PROGRESS_BG     = (30, 30, 42)
PROGRESS_FG     = (180, 180, 210)
DIVIDER_COLOR   = (35, 35, 50)

PADDING_X       = 64                    # horizontal text margin (each side)
HEADER_Y        = 28                    # top of header text
DIVIDER_Y       = 70                    # separator line y-position
PROGRESS_Y      = VIDEO_SIZE[1] - 28   # progress bar y-position
PROGRESS_H      = 3


# ── Font loading ─────────────────────────────────────────────────────────────────

# Noto Sans paths on Debian/Ubuntu (installed by the Dockerfile)
_FONT_CANDIDATES = [
    # Regular Noto Sans (Latin + Greek + Cyrillic + …)
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    # CJK fallback
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    # Generic downloaded fallback
    "/tmp/NotoSans.ttf",
]


def _load_system_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    # Download as a last resort (no network on Railway by default — this is a
    # safety net for local dev)
    fallback = "/tmp/NotoSans.ttf"
    if not os.path.exists(fallback):
        import requests  # noqa: PLC0415

        url = (
            "https://github.com/googlefonts/noto-fonts/raw/main/"
            "hinted/ttf/NotoSans/NotoSans-Regular.ttf"
        )
        logger.info("Downloading fallback font from %s", url)
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(fallback, "wb") as fh:
            fh.write(r.content)

    return ImageFont.truetype(fallback, size)


# ── Text helpers ─────────────────────────────────────────────────────────────────

def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * max(font.size // 2, 8)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """Word-wrap `text` so each line fits within `max_width` pixels."""
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    current: List[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if _text_width(font, candidate) > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    center_y: int,
    img_width: int,
    color: Tuple[int, int, int],
    max_width: int,
) -> int:
    """
    Draw a (possibly multi-line) centred text block.
    Returns the total pixel height consumed.
    """
    lines = _wrap(text, font, max_width)
    line_h = font.size + 10
    total_h = len(lines) * line_h
    y = center_y - total_h // 2

    for line in lines:
        w = _text_width(font, line)
        x = (img_width - w) // 2
        draw.text((x, y), line, font=font, fill=color)
        y += line_h

    return total_h


# ── Lyric navigation ─────────────────────────────────────────────────────────────

def _active_index(lyrics: List[Tuple[float, str]], t: float) -> int:
    """Return the index of the lyric line that is active at time `t`."""
    idx = 0
    for i, (ts, _) in enumerate(lyrics):
        if ts <= t:
            idx = i
        else:
            break
    return idx


# ── Main renderer ─────────────────────────────────────────────────────────────────

def make_lyric_video(
    audio_path: str,
    lyrics: List[Tuple[float, str]],
    start_sec: float,
    end_sec: float,
    output_path: str,
    title: str = "",
    artist: str = "",
) -> None:
    """
    Render a lyric video and write it to `output_path`.

    Parameters
    ----------
    audio_path : path to the downloaded mp3/m4a/etc.
    lyrics     : sorted list of (timestamp_seconds, line_text)
    start_sec  : clip start in seconds (relative to the full track)
    end_sec    : clip end in seconds
    output_path: destination .mp4 file
    title      : track title shown in the header
    artist     : artist name shown in the header
    """
    W, H = VIDEO_SIZE
    duration = end_sec - start_sec
    max_text_w = W - PADDING_X * 2

    # Pre-load fonts once (shared across all frames)
    font_header  = _load_system_font(22)
    font_current = _load_system_font(50)
    font_context = _load_system_font(33)

    # Build the header string once
    if artist and title:
        header_text: Optional[str] = f"{artist}  —  {title}"
    elif title or artist:
        header_text = title or artist
    else:
        header_text = None

    # ── frame factory ──────────────────────────────────────────────────────────
    def make_frame(t: float) -> np.ndarray:
        actual_t = t + start_sec
        idx = _active_index(lyrics, actual_t)

        prev_text    = lyrics[idx - 1][1] if idx > 0 else ""
        current_text = lyrics[idx][1]     if lyrics else ""
        next_text    = lyrics[idx + 1][1] if idx + 1 < len(lyrics) else ""

        img  = Image.new("RGB", (W, H), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Header
        if header_text:
            hw = _text_width(font_header, header_text)
            draw.text(((W - hw) // 2, HEADER_Y), header_text, font=font_header, fill=TITLE_COLOR)

        # Divider
        draw.line([(PADDING_X, DIVIDER_Y), (W - PADDING_X, DIVIDER_Y)], fill=DIVIDER_COLOR, width=1)

        # Lyric centre — stacked vertically in the usable zone
        usable_top    = DIVIDER_Y + 10
        usable_bottom = PROGRESS_Y - 15
        center_y      = (usable_top + usable_bottom) // 2

        # Previous line — 90 px above centre
        if prev_text:
            _draw_block(draw, prev_text, font_context,
                        center_y - 95, W, DIM_COLOR, max_text_w)

        # Current line — centred
        if current_text:
            _draw_block(draw, current_text, font_current,
                        center_y, W, HIGHLIGHT_COLOR, max_text_w)

        # Next line — 90 px below centre
        if next_text:
            _draw_block(draw, next_text, font_context,
                        center_y + 95, W, DIM_COLOR, max_text_w)

        # Progress bar
        bar_x0 = PADDING_X
        bar_x1 = W - PADDING_X
        draw.rectangle([(bar_x0, PROGRESS_Y), (bar_x1, PROGRESS_Y + PROGRESS_H)],
                       fill=PROGRESS_BG)
        fill_x = bar_x0 + int((bar_x1 - bar_x0) * min(t / max(duration, 1), 1.0))
        if fill_x > bar_x0:
            draw.rectangle([(bar_x0, PROGRESS_Y), (fill_x, PROGRESS_Y + PROGRESS_H)],
                           fill=PROGRESS_FG)

        return np.asarray(img)

    # ── assemble & export ──────────────────────────────────────────────────────
    logger.info("Rendering %s s of video at %d fps → %s", duration, FPS, output_path)

    audio_clip = AudioFileClip(audio_path)
    audio_dur  = audio_clip.duration
    safe_end   = min(end_sec, audio_dur)
    safe_dur   = max(safe_end - start_sec, 1.0)

    video_clip = VideoClip(make_frame, duration=safe_dur)
    audio_sub  = audio_clip.subclip(start_sec, safe_end)
    video_clip = video_clip.set_audio(audio_sub)

    video_clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        threads=4,
        logger=None,      # suppress moviepy's verbose output
    )

    audio_clip.close()
    audio_sub.close()
    video_clip.close()

    logger.info("Video written: %s", output_path)
