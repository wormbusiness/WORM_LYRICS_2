"""
Lyric Video Bot — main entry point.
Conversation flow:
  1. User sends a song query
  2. Bot searches lrclib.net and shows the best match
  3. User confirms or retries
  4. User provides a timestamp range (or "nil" for default 0:45–1:15)
  5. Bot downloads audio from SoundCloud, renders a lyric video, and sends it
"""

import asyncio
import logging
import os
import re
import tempfile

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from lyrics import search_lyrics, parse_lrc, distribute_plain_lyrics
from audio import search_and_download_soundcloud
from video import make_lyric_video

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ─────────────────────────────────────────────────────────
AWAITING_QUERY, AWAITING_CONFIRM, AWAITING_TIMESTAMP = range(3)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def parse_timestamp(text: str):
    """
    Parse a timestamp string like "1:20-2:05" into (start_sec, end_sec).
    Raises ValueError on bad input.
    """
    text = text.strip()
    parts = re.split(r"\s*[-–]\s*", text)
    if len(parts) != 2:
        raise ValueError("Expected format M:SS-M:SS")

    def to_seconds(t: str) -> float:
        t = t.strip()
        m, s = t.split(":")
        return int(m) * 60 + float(s)

    return to_seconds(parts[0]), to_seconds(parts[1])


# ── Handlers ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🎵 *Lyric Video Bot*\n\n"
        "Send me a song name (and optionally the artist) and I'll generate a lyric video clip for you.\n\n"
        "_Example:_ `Daft Punk Get Lucky`",
        parse_mode="Markdown",
    )
    return AWAITING_QUERY


async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("Please send a song name.")
        return AWAITING_QUERY

    context.user_data["query"] = query
    searching_msg = await update.message.reply_text("🔍 Searching for lyrics…")

    results = search_lyrics(query)
    if not results:
        await searching_msg.edit_text(
            "❌ No results found on lrclib.net. Try a more specific query."
        )
        return AWAITING_QUERY

    # Prefer results that have synced lyrics
    synced = [r for r in results if r.get("syncedLyrics")]
    chosen = synced[0] if synced else results[0]
    context.user_data["lyric_result"] = chosen

    title = chosen.get("trackName") or "Unknown title"
    artist = chosen.get("artistName") or "Unknown artist"
    album = chosen.get("albumName") or ""
    has_synced = bool(chosen.get("syncedLyrics"))
    duration_s = chosen.get("duration") or 0
    duration_fmt = f"{int(duration_s)//60}:{int(duration_s)%60:02d}" if duration_s else "?"

    info_lines = [
        f"🎵 *{title}*",
        f"👤 {artist}",
    ]
    if album:
        info_lines.append(f"💿 {album}")
    info_lines.append(f"⏱ Duration: {duration_fmt}")
    info_lines.append(f"📝 Synced lyrics: {'✅' if has_synced else '⚠️ plain only'}")
    info_lines.append("\nIs this the song you want?")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, use this", callback_data="confirm")],
        [InlineKeyboardButton("🔄 Try a different search", callback_data="retry")],
    ])

    await searching_msg.edit_text(
        "\n".join(info_lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return AWAITING_CONFIRM


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "retry":
        await query.edit_message_text("Okay! Send me a new search query:")
        return AWAITING_QUERY

    # User confirmed — ask for timestamp
    lyric_result = context.user_data.get("lyric_result", {})
    duration_s = lyric_result.get("duration") or 0

    hint = ""
    if duration_s:
        safe_end = min(75, int(duration_s) - 5)
        safe_start = max(0, safe_end - 30)
        hint = f"\n_Song is {int(duration_s)//60}:{int(duration_s)%60:02d} — suggested: `{safe_start//60}:{safe_start%60:02d}-{safe_end//60}:{safe_end%60:02d}`_"

    await query.edit_message_text(
        f"⏱ *Set the clip timestamp*\n\n"
        f"Enter the range as `M:SS-M:SS`\n"
        f"_Example:_ `0:45-1:15` _(30-second clip)_\n"
        f"{hint}\n\n"
        f"Or type `nil` to use the default `0:45-1:15`.",
        parse_mode="Markdown",
    )
    return AWAITING_TIMESTAMP


async def handle_timestamp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.lower() == "nil":
        start_sec, end_sec = 45.0, 75.0
    else:
        try:
            start_sec, end_sec = parse_timestamp(text)
        except Exception:
            await update.message.reply_text(
                "❌ Couldn't parse that. Use `M:SS-M:SS` (e.g. `1:10-1:45`) or `nil`.",
                parse_mode="Markdown",
            )
            return AWAITING_TIMESTAMP

    if end_sec <= start_sec:
        await update.message.reply_text("❌ End time must be after start time.")
        return AWAITING_TIMESTAMP

    if end_sec - start_sec > 120:
        await update.message.reply_text("❌ Clips longer than 2 minutes aren't supported.")
        return AWAITING_TIMESTAMP

    context.user_data["start_sec"] = start_sec
    context.user_data["end_sec"] = end_sec

    progress_msg = await update.message.reply_text(
        "🎬 Got it! Downloading audio and rendering your lyric video…\n"
        "_(This usually takes 30–90 seconds.)_",
        parse_mode="Markdown",
    )

    # Run the heavy work in a thread so the event loop stays responsive
    loop = asyncio.get_event_loop()
    try:
        video_path = await loop.run_in_executor(None, _build_video, context.user_data)
    except Exception as exc:
        logger.exception("Video build failed")
        await progress_msg.edit_text(f"❌ Something went wrong:\n`{exc}`", parse_mode="Markdown")
        return ConversationHandler.END

    lyric_result = context.user_data["lyric_result"]
    title = lyric_result.get("trackName") or "Unknown"
    artist = lyric_result.get("artistName") or ""

    try:
        with open(video_path, "rb") as f:
            await update.message.reply_video(
                f,
                caption=f"🎵 *{title}*" + (f"\n👤 {artist}" if artist else ""),
                parse_mode="Markdown",
                supports_streaming=True,
            )
        await progress_msg.delete()
    except Exception as exc:
        logger.exception("Failed to send video")
        await progress_msg.edit_text(f"❌ Failed to send video:\n`{exc}`", parse_mode="Markdown")
    finally:
        try:
            os.remove(video_path)
        except OSError:
            pass

    return ConversationHandler.END


# ── Synchronous worker (runs in thread pool) ─────────────────────────────────────

def _build_video(user_data: dict) -> str:
    """Download audio, parse lyrics, render video. Returns path to the output file."""
    lyric_result = user_data["lyric_result"]
    start_sec: float = user_data["start_sec"]
    end_sec: float = user_data["end_sec"]

    title = lyric_result.get("trackName") or ""
    artist = lyric_result.get("artistName") or ""
    synced_lrc = lyric_result.get("syncedLyrics") or ""
    plain_lyrics = lyric_result.get("plainLyrics") or ""

    # ── 1. Parse lyrics ──
    if synced_lrc:
        lyrics = parse_lrc(synced_lrc)
    elif plain_lyrics:
        lyrics = distribute_plain_lyrics(plain_lyrics, start_sec, end_sec)
    else:
        raise RuntimeError("No lyrics available for this track.")

    if not lyrics:
        raise RuntimeError("Lyrics parsed to an empty list.")

    # ── 2. Download audio from SoundCloud ──
    sc_query = f"{artist} {title}".strip() or user_data.get("query", "")
    audio_info = search_and_download_soundcloud(sc_query)
    audio_path = audio_info["file"]

    # ── 3. Clamp timestamps to actual audio duration ──
    audio_dur = audio_info.get("duration") or (end_sec + 10)
    clamped_end = min(end_sec, audio_dur - 0.5)
    if clamped_end <= start_sec:
        start_sec = max(0, clamped_end - 30)

    # ── 4. Render video ──
    out_fd, out_path = tempfile.mkstemp(suffix=".mp4", prefix="lyrv_")
    os.close(out_fd)

    try:
        make_lyric_video(
            audio_path=audio_path,
            lyrics=lyrics,
            start_sec=start_sec,
            end_sec=clamped_end,
            output_path=out_path,
            title=title,
            artist=artist,
        )
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    return out_path


# ── Application bootstrap ────────────────────────────────────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query),
        ],
        states={
            AWAITING_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query)
            ],
            AWAITING_CONFIRM: [
                CallbackQueryHandler(handle_confirm)
            ],
            AWAITING_TIMESTAMP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timestamp)
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("Bot is running (polling)…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
