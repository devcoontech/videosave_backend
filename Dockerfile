FROM python:3.11-slim

# Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]"

COPY . .

# Create persistent download and temporary directories
RUN mkdir -p /app/downloads /app/temp

# Establish package resolution symlink so import backend.app resolves cleanly
RUN mkdir -p /app/backend && ln -s /app/app /app/backend/app

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
