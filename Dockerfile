FROM python:3.11-slim

# Install system dependencies including FFmpeg, Node (bgutil PO tokens), and canvas build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    git \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && git clone --single-branch --branch 1.3.2 --depth 1 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server && npm ci && npx tsc \
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
ENV BGUTIL_POT_BASE_URL=http://127.0.0.1:4416
EXPOSE 8000

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js --host 127.0.0.1 & sleep 3 && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
