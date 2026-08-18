from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            values.add(int(part))
    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    queue_name: str = os.getenv("QUEUE_NAME", "media_jobs")
    clear_queue_on_worker_start: bool = _bool("CLEAR_QUEUE_ON_WORKER_START", True)
    max_file_mb: int = _int("MAX_FILE_MB", 50)
    video_codec_mode: str = os.getenv("VIDEO_CODEC_MODE", "mp4").strip().lower()
    youtube_multi_audio: bool = _bool("YOUTUBE_MULTI_AUDIO", False)
    youtube_audio_language: str = os.getenv("YOUTUBE_AUDIO_LANGUAGE", "").strip().lower()
    youtube_player_client: str = os.getenv("YOUTUBE_PLAYER_CLIENT", "web_embedded").strip().lower()
    youtube_sponsorblock_caption: bool = _bool("YOUTUBE_SPONSORBLOCK_CAPTION", False)
    youtube_sponsorblock_categories: str = os.getenv("YOUTUBE_SPONSORBLOCK_CATEGORIES", "sponsor,selfpromo,interaction").strip().lower()
    media_audio_normalize: bool = _bool("MEDIA_AUDIO_NORMALIZE", _bool("YOUTUBE_AUDIO_NORMALIZE", True))
    media_audio_loudnorm_i: int = _int("MEDIA_AUDIO_LOUDNORM_I", _int("YOUTUBE_AUDIO_LOUDNORM_I", -16))
    media_audio_loudnorm_tp: int = _int("MEDIA_AUDIO_LOUDNORM_TP", _int("YOUTUBE_AUDIO_LOUDNORM_TP", -1))
    media_audio_loudnorm_lra: int = _int("MEDIA_AUDIO_LOUDNORM_LRA", _int("YOUTUBE_AUDIO_LOUDNORM_LRA", 11))
    telegram_api_base_url: str = os.getenv("TELEGRAM_API_BASE_URL", "")
    telegram_api_file_url: str = os.getenv("TELEGRAM_API_FILE_URL", "")
    telegram_request_timeout_seconds: int = _int("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 600)
    max_duration_seconds: int = _int("MAX_DURATION_SECONDS", 7200)
    download_timeout_seconds: int = _int("DOWNLOAD_TIMEOUT_SECONDS", 600)
    job_ttl_seconds: int = _int("JOB_TTL_SECONDS", 1800)
    chat_cooldown_seconds: int = _int("CHAT_COOLDOWN_SECONDS", 20)
    active_job_idle_timeout_seconds: int = _int("ACTIVE_JOB_IDLE_TIMEOUT_SECONDS", 1800)
    max_urls_per_message: int = _int("MAX_URLS_PER_MESSAGE", 5)
    max_pending_jobs_per_chat: int = _int("MAX_PENDING_JOBS_PER_CHAT", 20)
    max_pending_jobs_per_user: int = _int("MAX_PENDING_JOBS_PER_USER", 5)
    pending_job_ttl_seconds: int = _int("PENDING_JOB_TTL_SECONDS", 1800)
    media_cache_ttl_seconds: int = _int("MEDIA_CACHE_TTL_SECONDS", 7776000)
    ytdlp_update_interval_seconds: int = _int("YTDLP_UPDATE_INTERVAL_SECONDS", 21600)
    ytdlp_update_poll_seconds: int = _int("YTDLP_UPDATE_POLL_SECONDS", 10)
    ytdlp_restart_after_update: bool = _bool("YTDLP_RESTART_AFTER_UPDATE", False)
    ytdlp_bin: str = os.getenv("YTDLP_BIN", "/opt/ytdlp/yt-dlp")
    data_dir: str = os.getenv("DATA_DIR", "/app/data")
    auth_browser_url: str = os.getenv("AUTH_BROWSER_URL", "http://127.0.0.1:33000/")
    browser_profile_path: str = os.getenv("BROWSER_PROFILE_PATH", "/browser-profile/.config/chromium")
    bgutil_base_url: str = os.getenv("BGUTIL_BASE_URL", "http://bgutil:4416")
    admin_ids: frozenset[int] = _int_set("ADMIN_IDS")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


settings = Settings()
