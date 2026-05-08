# ──────────────────────────────────────────────────────────────────────────────
# Lyric Video Bot — Dockerfile
#
# Key system deps:
#   ffmpeg          — audio/video encoding (required by moviepy + yt-dlp)
#   fonts-noto      — Noto Sans for Latin / Greek / Cyrillic / Arabic etc.
#   fonts-noto-cjk  — Noto Sans CJK for Chinese / Japanese / Korean
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Prevent interactive prompts during apt-get
ENV DEBIAN_FRONTEND=noninteractive

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto \
        fonts-noto-cjk \
        # Needed by Pillow for some image operations
        libfreetype6 \
        # Clean up
    && rm -rf /var/lib/apt/lists/*

# Refresh font cache so PIL/Pillow can find the Noto fonts
RUN fc-cache -fv

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY *.py ./

# Non-root user for security
RUN useradd -m botuser && chown -R botuser /app
USER botuser

# Telegram polling — no port needed
CMD ["python", "main.py"]
