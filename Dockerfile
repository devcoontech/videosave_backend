FROM python:3.11-slim

# FFmpeg + Node 22 (bgutil requires >=22) + canvas build deps for bgutil
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
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && git clone --single-branch --branch 1.3.2 --depth 1 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server && npm ci && npx tsc \
    && test -f /opt/bgutil/server/build/main.js \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]"

COPY . .

RUN mkdir -p /app/downloads /app/temp \
    && mkdir -p /app/backend && ln -s /app/app /app/backend/app \
    && chmod +x /app/start.sh

ENV PYTHONPATH=/app
ENV BGUTIL_POT_BASE_URL=http://127.0.0.1:4416
EXPOSE 8000

CMD ["/app/start.sh"]
