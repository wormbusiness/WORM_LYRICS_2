# 🎵 Lyric Video Bot — Deployment Guide

A Telegram bot that takes a song query, fetches synced lyrics from **lrclib.net**, downloads audio from **SoundCloud**, and renders a square lyric video clip that it sends back to the user.

---

## How the Bot Works

```
User sends song query
      ↓
Bot searches lrclib.net for synced lyrics
      ↓
User confirms the match (or retries)
      ↓
User provides a clip timestamp  (or types "nil" → defaults to 0:45–1:15)
      ↓
Bot downloads audio from SoundCloud via yt-dlp
      ↓
Bot renders a 720×720 MP4 — lyrics synced to audio
      ↓
Video is sent back to the user
```

---

## Project Structure

```
lyric-video-bot/
├── main.py           # Telegram bot + conversation flow
├── lyrics.py         # lrclib.net API + LRC parsing
├── audio.py          # SoundCloud search + download (yt-dlp)
├── video.py          # Lyric video renderer (PIL + MoviePy)
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env.example
```

---

## Prerequisites

| Tool | Purpose |
|------|---------|
| [Railway account](https://railway.app) | Hosting |
| [GitHub account](https://github.com) | Source repo (Railway deploys from GitHub) |
| Telegram account | To create and use the bot |

---

## Step 1 — Create Your Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts:
   - Choose a display name (e.g. `Lyric Video Bot`)
   - Choose a username ending in `bot` (e.g. `mylyricvideobot`)
3. BotFather replies with your **bot token** — save it, you'll need it shortly. It looks like:
   ```
   7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

## Step 2 — Push the Code to GitHub

1. Create a new **private** repository on GitHub (e.g. `lyric-video-bot`).

2. Clone it locally and copy all project files into the repo root:
   ```bash
   git clone https://github.com/YOUR_USERNAME/lyric-video-bot.git
   cd lyric-video-bot

   # Copy the files (main.py, lyrics.py, audio.py, video.py,
   # requirements.txt, Dockerfile, railway.toml, .gitignore)
   ```

3. Commit and push:
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

> ⚠️ **Never** commit your `.env` file or bot token to Git.

---

## Step 3 — Deploy to Railway

### 3a. Create a New Project

1. Go to [railway.app](https://railway.app) and sign in.
2. Click **New Project → Deploy from GitHub repo**.
3. Authorise Railway to access your GitHub account if prompted.
4. Select your `lyric-video-bot` repository.

### 3b. Configure Environment Variables

In your Railway project dashboard:

1. Click your service → **Variables** tab.
2. Add the following variable:

   | Variable | Value |
   |----------|-------|
   | `TELEGRAM_BOT_TOKEN` | The token you got from BotFather |

3. Click **Add** and Railway will automatically redeploy.

### 3c. Watch the Build

- Click the **Deployments** tab to watch the build log.
- The Dockerfile installs `ffmpeg`, Noto fonts, and all Python packages — the first build takes **3–5 minutes**.
- Once you see `Bot is running (polling)…` in the logs, you're live.

---

## Step 4 — Test Your Bot

1. Open Telegram and search for your bot by its username.
2. Send `/start` — the bot should greet you.
3. Send a song query, e.g.:
   ```
   Daft Punk Get Lucky
   ```
4. Confirm the result with the ✅ button.
5. Enter a timestamp like `1:00-1:30` or send `nil` for the default.
6. Wait ~30–90 seconds — the bot will send you a video!

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ Yes | Token from @BotFather |

No other variables are needed. Audio files and rendered videos are written to `/tmp` inside the container and cleaned up after each request.

---

## Local Development

### Requirements

- Python 3.11+
- `ffmpeg` installed on your machine (`brew install ffmpeg` / `apt install ffmpeg`)
- Noto fonts installed (or the bot will download a fallback at runtime)

### Setup

```bash
# Clone your repo
git clone https://github.com/YOUR_USERNAME/lyric-video-bot.git
cd lyric-video-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set your token
cp .env.example .env
# Edit .env and paste your TELEGRAM_BOT_TOKEN

# Load env and run
export $(cat .env | xargs)
python main.py
```

---

## How the Lyric Video is Rendered

The video renderer (`video.py`) works like this:

1. **Frame factory** — for every video frame (24 fps), it computes `actual_time = frame_time + clip_start`.
2. **Active lyric** — using the parsed LRC timestamps, it finds which lyric line is current.
3. **Three-line layout** — the previous line is shown dimmed above centre, the current line highlighted in gold at centre, and the next line dimmed below.
4. **Text wrapping** — long lines are word-wrapped to stay within the 720 px frame.
5. **Multilingual fonts** — Noto Sans (Latin/Greek/Cyrillic/Arabic/etc.) and Noto Sans CJK (Chinese/Japanese/Korean) are installed in the Docker image, so most scripts render correctly.
6. **Audio** — the SoundCloud mp3 is trimmed to the requested clip and muxed in via MoviePy/ffmpeg.
7. **Output** — a 720×720 H.264/AAC MP4 at 24 fps.

---

## Troubleshooting

### Build fails on Railway

- Check the build log for the exact error.
- Most common cause: a Python package version conflict. Try removing version pins from `requirements.txt` for the offending package.

### "No results found" from lrclib.net

- lrclib.net is a community lyrics database. Not all songs are indexed.
- Try adding the artist name: `Taylor Swift Anti-Hero` instead of just `Anti-Hero`.
- Instrumental tracks won't have lyrics.

### Video is silent or has wrong audio

- SoundCloud results depend on what yt-dlp finds. If the top result is a remix or cover, refine your query (e.g. add `"official"` or the album name).
- Some SoundCloud tracks block downloading. yt-dlp will fall back to the next result automatically.

### "Synced lyrics: ⚠️ plain only"

- lrclib.net has the track but only with plain (un-timed) lyrics.
- The bot will still create a video — it distributes lines evenly across the clip duration.
- For better sync, find the same song on a streaming app, note the exact title/artist spelling, and retry.

### Bot stops responding after Railway restart

- Railway restarts containers on deploy or failure. The bot uses `ON_FAILURE` restart policy.
- If the bot is offline, check the Railway dashboard → **Deployments** for crash logs.
- Conversation state is in-memory and lost on restart — users will need to start a new query.

### Video takes too long / Railway times out

- Rendering a 30-second clip at 24 fps takes 30–90 seconds of CPU time.
- Railway's Hobby plan provides sufficient CPU. If builds seem throttled, check your plan limits.
- Avoid requesting clips longer than 60 seconds; they'll be slow and produce large files.

---

## Upgrading & Customisation

### Change the video resolution

Edit `VIDEO_SIZE` in `video.py`:
```python
VIDEO_SIZE = (1080, 1080)  # or (720, 720) for smaller files
```

### Change the default timestamp

Edit `handle_timestamp` in `main.py`:
```python
if text.lower() == "nil":
    start_sec, end_sec = 45.0, 75.0  # ← change these
```

### Change colours / font sizes

All visual constants are at the top of `video.py`:
```python
HIGHLIGHT_COLOR = (255, 215, 60)   # active lyric colour
DIM_COLOR       = (85, 85, 100)    # prev/next lyric colour
```

### Add a custom font

Place a `.ttf` file in the repo and update `_FONT_CANDIDATES` in `video.py`:
```python
_FONT_CANDIDATES = [
    "/app/MyFont.ttf",   # ← add this first
    ...
]
```
Then add it to the Dockerfile:
```dockerfile
COPY MyFont.ttf /app/MyFont.ttf
```

---

## Cost Estimate (Railway)

| Plan | Monthly cost | Suitable? |
|------|-------------|-----------|
| Hobby ($5/mo) | ~$5 + usage | ✅ Great for personal use |
| Pro | $20/mo + usage | ✅ For higher traffic |

Rendering is CPU-intensive. A 30-second clip uses roughly 1–2 minutes of CPU time. On the Hobby plan this is well within the free credit for personal use.

---

## Security Notes

- The bot token is read from the environment — never hardcode it.
- Audio files and rendered videos are deleted from `/tmp` immediately after sending.
- The bot runs as a non-root user inside the Docker container.
- No user data is stored anywhere.
