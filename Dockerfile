FROM brainicism/bgutil-ytdlp-pot-provider:1.3.2-node AS bgutil

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libjpeg62-turbo \
    libgif7 \
    librsvg2-2 \
    libfontconfig1 \
    libpixman-1-0 \
    libfreetype6 \
    libglib2.0-0 \
    libpng16-16 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# bgutil node_modules (canvas, etc.) must run on the same Node major as the bgutil image.
COPY --from=bgutil /usr/local/bin/node /usr/local/bin/node

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
    && test -f /opt/bgutil-app/build/main.js \
    && test -f /opt/bgutil-app/build/generate_once.js \
    && node /opt/bgutil-app/build/generate_once.js --version \
    && node -e "require('/opt/bgutil-app/node_modules/canvas'); console.log('canvas ok')" \
    && python -c "import pathlib; assert list(pathlib.Path('/usr/local/lib/python3.11/site-packages/yt_dlp_plugins/extractor').glob('getpot_bgutil*.py')), 'bgutil plugin missing'"

ENV PYTHONPATH=/app
ENV BGUTIL_POT_BASE_URL=http://127.0.0.1:4416
ENV YOUTUBE_COOKIES_FIRST=false
ENV HOME=/tmp
ENV XDG_CACHE_HOME=/tmp/bgutil-cache
EXPOSE 8000

CMD ["/app/start.sh"]
