FROM brainicism/bgutil-ytdlp-pot-provider:1.3.2-node AS bgutil

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Official image layout: /app/build/main.js + /app/node_modules
COPY --from=bgutil /app /opt/bgutil-app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]"

COPY . .

RUN mkdir -p /app/downloads /app/temp \
    && mkdir -p /app/backend && ln -s /app/app /app/backend/app \
    && chmod +x /app/start.sh \
    && test -f /opt/bgutil-app/build/main.js

ENV PYTHONPATH=/app
ENV BGUTIL_POT_BASE_URL=http://127.0.0.1:4416
ENV YOUTUBE_COOKIES_FIRST=false
EXPOSE 8000

CMD ["/app/start.sh"]
