FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YTDLP_BIN=/opt/ytdlp/yt-dlp
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr sh \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && mkdir -p /opt/ytdlp \
    && cp "$(command -v yt-dlp)" /opt/ytdlp/yt-dlp \
    && chmod +x /opt/ytdlp/yt-dlp
COPY app ./app
RUN mkdir -p /app/downloads /opt/ytdlp
CMD ["python", "-m", "app.bot"]
