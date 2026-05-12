from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import settings

log = logging.getLogger(__name__)
DOWNLOAD_DIR = Path("/app/downloads") if Path("/app").exists() else Path(__file__).resolve().parents[1] / "downloads"
DATA_DIR = Path(settings.data_dir)
COOKIES_DIR = DATA_DIR / "cookies"
GALLERY_DL_CONFIG = DATA_DIR / "gallery-dl.conf"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


class DownloadRejected(Exception):
    pass


class DownloadFailed(Exception):
    pass


@dataclass(frozen=True)
class DownloadItem:
    path: Path
    media_type: Literal["photo", "video", "document"]
    caption: str | None = None
    thumbnail_path: Path | None = None
    cover_path: Path | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    media_type: Literal["photo", "video", "document"]
    caption: str | None
    extractor: str = "unknown"
    items: tuple[DownloadItem, ...] = ()

    def all_items(self) -> tuple[DownloadItem, ...]:
        return self.items or (DownloadItem(self.path, self.media_type, self.caption),)


def _base_opts(workdir: Path) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "cachedir": False,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 8,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "paths": {"home": str(workdir)},
        "overwrites": True,
        "external_downloader_args": {"ffmpeg_i": ["-c", "copy"]},
    }
    cookies = _cookie_file_for_url(workdir)
    # Placeholder, replaced by _ytdlp_opts_for_url where URL is known.
    if cookies:
        opts["cookiefile"] = str(cookies)
    return opts


def _validate_info(info: dict[str, Any], *, allow_images: bool = False, max_duration_seconds: int | None = None) -> None:
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
        raise DownloadRejected("Плейлисты и альбомы пока не поддерживаются.")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise DownloadRejected("Live-видео не поддерживаются.")
    limit = max_duration_seconds if max_duration_seconds is not None else settings.max_duration_seconds
    if limit > 0:
        duration = info.get("duration")
        if duration and float(duration) > limit:
            raise DownloadRejected(f"Видео длиннее лимита {limit} сек.")
    if not allow_images and info.get("vcodec") == "none":
        raise DownloadFailed("В посте нет видео.")


def _filesize_ok(path: Path) -> bool:
    return path.exists() and path.stat().st_size <= settings.max_file_bytes


def _format_height(fmt: dict[str, Any]) -> int:
    return int(fmt.get("height") or 0)


def _format_size_bytes(fmt: dict[str, Any], duration: float | None = None) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    tbr = fmt.get("tbr")
    if tbr and duration:
        # tbr is KBit/s; add 10% container/metadata safety margin.
        return int(float(tbr) * 1000 / 8 * float(duration) * 1.10)
    return None


def _is_premerged_h264_aac(fmt: dict[str, Any]) -> bool:
    vcodec = str(fmt.get("vcodec") or "")
    acodec = str(fmt.get("acodec") or "")
    return fmt.get("ext") == "mp4" and vcodec.startswith("avc1") and acodec.startswith("mp4a")


def _is_video_only_codec(fmt: dict[str, Any], codec_mode: str) -> bool:
    vcodec = str(fmt.get("vcodec") or "")
    if fmt.get("acodec") != "none":
        return False
    if codec_mode == "av1":
        return fmt.get("ext") == "mp4" and vcodec.startswith("av01")
    if codec_mode in {"vp9", "av9"}:
        return fmt.get("ext") == "webm" and vcodec == "vp9"
    return False


def _youtube_extractor_args() -> dict[str, dict[str, list[str]]]:
    if settings.youtube_multi_audio and settings.youtube_player_client:
        return {"youtube": {"player_client": [settings.youtube_player_client]}}
    return {}


def _language_audio_selectors(base_selector: str, *, ext: str | None = None, acodec: str | None = None) -> str:
    lang = settings.youtube_audio_language
    if not settings.youtube_multi_audio or not lang:
        return base_selector
    filters = [f"language^={lang}"]
    if ext:
        filters.append(f"ext={ext}")
    if acodec:
        filters.append(acodec)
    preferred = "bestaudio" + "".join(f"[{flt}]" for flt in filters)
    loose = f"bestaudio[language^={lang}]"
    return f"{preferred}/{loose}/{base_selector}"


def _audio_format_for_codec(codec_mode: str) -> str:
    if codec_mode in {"vp9", "av9"}:
        base = "bestaudio[ext=webm][acodec=opus]/bestaudio[acodec=opus]/bestaudio"
        return _language_audio_selectors(base, ext="webm", acodec="acodec=opus")
    base = "bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[acodec^=mp4a]/bestaudio"
    return _language_audio_selectors(base, ext="m4a", acodec="acodec^=mp4a")


def _video_with_audio_selector(video_selector: str, codec_mode: str) -> str:
    return "/".join(f"{video_selector}+{audio}" for audio in _audio_format_for_codec(codec_mode).split("/"))


def _merge_output_format_for_mode(codec_mode: str) -> str:
    return "webm" if codec_mode in {"vp9", "av9"} else "mp4"


def _video_suffixes_for_mode(codec_mode: str) -> set[str]:
    return {".webm"} if codec_mode in {"vp9", "av9"} else {".mp4"}


def _is_supported_video_file(path: Path, codec_mode: str) -> bool:
    suffix = path.suffix.lower()
    return suffix in _video_suffixes_for_mode(codec_mode)


def _fallback_selector_for_mode(codec_mode: str) -> str:
    if codec_mode == "av1":
        return "/".join([
            _video_with_audio_selector("bestvideo[ext=mp4][vcodec^=av01][height<=480]", codec_mode),
            _video_with_audio_selector("bestvideo[ext=mp4][vcodec^=av01][height<=360]", codec_mode),
        ])
    if codec_mode in {"vp9", "av9"}:
        return "/".join([
            _video_with_audio_selector("bestvideo[ext=webm][vcodec=vp9][height<=480]", codec_mode),
            _video_with_audio_selector("bestvideo[ext=webm][vcodec=vp9][height<=360]", codec_mode),
        ])
    if settings.youtube_multi_audio and settings.youtube_audio_language:
        return "/".join([
            _video_with_audio_selector("bestvideo[ext=mp4][vcodec^=avc1][height<=480]", codec_mode),
            _video_with_audio_selector("bestvideo[ext=mp4][vcodec^=avc1][height<=360]", codec_mode),
        ])
    return "best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=480]/best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=360]/best[ext=mp4][vcodec^=avc1][acodec^=mp4a]"


def _codec_mode() -> str:
    mode = settings.video_codec_mode
    if mode == "auto":
        return "mp4"
    return mode if mode in {"mp4", "av1", "vp9", "av9"} else "mp4"


def _select_safe_telegram_format(info: dict[str, Any], max_bytes: int) -> tuple[str, bool, str]:
    duration = info.get("duration")
    duration_f = float(duration) if duration else None
    mode = _codec_mode()

    if mode in {"av1", "vp9", "av9"}:
        video_candidates = [f for f in info.get("formats") or [] if _is_video_only_codec(f, mode)]
        audio_candidates = [f for f in info.get("formats") or [] if f.get("acodec") != "none" and f.get("vcodec") == "none"]
        audio_size = min((_format_size_bytes(f, duration_f) or 0 for f in audio_candidates), default=0)
        for max_height in (480, 360):
            matching = [f for f in video_candidates if 0 < _format_height(f) <= max_height]
            for fmt in sorted(matching, key=_format_height, reverse=True):
                size = _format_size_bytes(fmt, duration_f)
                total_size = (size or 0) + audio_size if size else None
                if total_size is None or total_size <= max_bytes:
                    selector = _video_with_audio_selector(str(fmt.get("format_id")), mode)
                    log.info("selected safe yt-dlp format id=%s mode=%s height=%s estimated_size=%s max=%s", fmt.get("format_id"), mode, fmt.get("height"), total_size, max_bytes)
                    return selector, True, mode
        raise DownloadRejected(f"Даже 360p в режиме {mode} по оценке больше {settings.max_file_mb} MB.")


    if settings.youtube_multi_audio and settings.youtube_audio_language:
        video_candidates = [
            f for f in info.get("formats") or []
            if f.get("ext") == "mp4" and str(f.get("vcodec") or "").startswith("avc1") and f.get("acodec") == "none"
        ]
        audio_candidates = [f for f in info.get("formats") or [] if f.get("acodec") != "none" and f.get("vcodec") == "none"]
        audio_size = min((_format_size_bytes(f, duration_f) or 0 for f in audio_candidates), default=0)
        for max_height in (480, 360):
            matching = [f for f in video_candidates if 0 < _format_height(f) <= max_height]
            for fmt in sorted(matching, key=_format_height, reverse=True):
                size = _format_size_bytes(fmt, duration_f)
                total_size = (size or 0) + audio_size if size else None
                if total_size is None or total_size <= max_bytes:
                    selector = _video_with_audio_selector(str(fmt.get("format_id")), "mp4")
                    log.info(
                        "selected safe yt-dlp format id=%s mode=mp4 audio_language=%s height=%s estimated_size=%s max=%s",
                        fmt.get("format_id"),
                        settings.youtube_audio_language,
                        fmt.get("height"),
                        total_size,
                        max_bytes,
                    )
                    return selector, True, "mp4"
        log.warning("no safe h264 video-only format for requested YouTube audio language; falling back to premerged mp4")

    candidates = [f for f in info.get("formats") or [] if _is_premerged_h264_aac(f)]
    if not candidates:
        return _fallback_selector_for_mode("mp4"), False, "mp4"

    for max_height in (480, 360):
        matching = [f for f in candidates if 0 < _format_height(f) <= max_height]
        for fmt in sorted(matching, key=_format_height, reverse=True):
            size = _format_size_bytes(fmt, duration_f)
            if size is None or size <= max_bytes:
                log.info("selected safe yt-dlp format id=%s mode=mp4 height=%s estimated_size=%s max=%s", fmt.get("format_id"), fmt.get("height"), size, max_bytes)
                return str(fmt.get("format_id")), False, "mp4"

    raise DownloadRejected(f"Даже 360p по оценке больше {settings.max_file_mb} MB.")


def _clean_caption(text: str) -> str | None:
    text = html.unescape(text).strip()
    if not text or text.lower() in {"none", "untitled"}:
        return None
    text = " ".join(text.split())
    if len(text) > 900:
        text = text[:897].rstrip() + "…"
    return text


def _fmt_time(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _chapters_text(chapters: list[dict] | None) -> str | None:
    if not chapters:
        return None
    lines = []
    for ch in chapters:
        ts = _fmt_time(ch["start_time"])
        title = ch.get("title", "")
        lines.append(f"{ts} {title}" if title else f"{ts}")
    return "\n".join(lines) if lines else None



_SPONSORBLOCK_CATEGORY_LABELS = {
    "sponsor": "спонсорская вставка",
    "selfpromo": "самореклама",
    "interaction": "лайк/подписка",
    "intro": "интро",
    "outro": "аутро",
    "preview": "превью",
    "music_offtopic": "не по теме",
    "filler": "филлер",
}


def _sponsorblock_categories() -> list[str]:
    return [part.strip() for part in settings.youtube_sponsorblock_categories.split(",") if part.strip()]


def _sponsorblock_segments(video_id: str | None) -> list[dict[str, Any]]:
    if not settings.youtube_sponsorblock_caption or not video_id:
        return []
    categories = _sponsorblock_categories()
    if not categories:
        return []
    query = urlencode({"videoID": video_id, "categories": json.dumps(categories)})
    url = f"https://sponsor.ajay.app/api/skipSegments?{query}"
    try:
        with urlopen(url, timeout=10) as response:
            if response.status == 404:
                return []
            if response.status != 200:
                log.warning("SponsorBlock returned status=%s for video=%s", response.status, video_id)
                return []
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - SponsorBlock is optional metadata
        log.warning("SponsorBlock lookup failed for video=%s: %s", video_id, exc)
        return []
    if not isinstance(data, list):
        return []
    return [seg for seg in data if isinstance(seg, dict) and isinstance(seg.get("segment"), list) and len(seg["segment"]) >= 2]


def _sponsorblock_text(info: dict[str, Any]) -> str | None:
    segments = _sponsorblock_segments(str(info.get("id") or "") or None)
    if not segments:
        return None
    lines = ["Пропуск рекламы:"]
    for seg in sorted(segments, key=lambda item: float(item.get("segment", [0])[0] or 0))[:12]:
        start, end = seg["segment"][:2]
        category = str(seg.get("category") or "")
        label = _SPONSORBLOCK_CATEGORY_LABELS.get(category, category or "segment")
        # Telegram распознаёт отдельные таймкоды в подписи к видео.
        # Главный таймкод — конец сегмента: тап по нему переносит сразу после рекламы.
        lines.append(f"⏭ {_fmt_time(float(end))} — после: {label} (с {_fmt_time(float(start))})")
    return "\n".join(lines)


def _caption_from_info(info: dict[str, Any]) -> str | None:
    caption = _clean_caption(str(info.get("title") or info.get("description") or ""))
    parts = [caption] if caption else []
    chapters = info.get("chapters")
    if chapters:
        ch_text = _chapters_text(chapters)
        if ch_text:
            parts.append(ch_text)
    sb_text = _sponsorblock_text(info)
    if sb_text:
        parts.append(sb_text)
    result = "\n\n".join(parts) if parts else None
    if result and len(result) > 1024:
        result = result[:1021].rstrip() + "…"
    return result


def _host_platform(url: str) -> str | None:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    if "instagram.com" in host or "instagr.am" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "vk.com" in host or "vkvideo.ru" in host:
        return "vk"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    return None


def _cookie_file_for_url(url_or_path: str | Path) -> Path | None:
    if isinstance(url_or_path, Path):
        return None
    platform = _host_platform(url_or_path)
    candidates = []
    if platform:
        candidates.append(COOKIES_DIR / f"{platform}.txt")
    candidates.append(COOKIES_DIR / "cookies.txt")
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _find_file(workdir: Path, suffixes: set[str], *, largest: bool = False) -> Path | None:
    files = sorted(
        (p for p in workdir.rglob("*") if p.is_file() and p.suffix.lower() in suffixes),
        key=lambda p: p.stat().st_size if p.exists() else 0,
        reverse=largest,
    )
    for path in files:
        if _filesize_ok(path):
            return path
    return None


def _find_gallery_media(workdir: Path) -> Path | None:
    # Prefer actual video over thumbnails, then largest image.
    files = _find_gallery_media_files(workdir)
    return files[0] if files else None


def _find_gallery_media_files(workdir: Path) -> list[Path]:
    files = [
        p
        for p in workdir.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS and _filesize_ok(p)
    ]
    # Keep gallery/story order when possible. Fall back to path name for stable output.
    files.sort(key=lambda p: str(p.relative_to(workdir)))
    return files


def _run_ffprobe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _is_telegram_mp4(path: Path) -> bool:
    data = _run_ffprobe(path)
    streams = data.get("streams") or []
    has_h264 = any(s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in streams)
    has_aac = any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams)
    return has_h264 and has_aac


def describe_media(path: Path) -> str:
    if path.suffix.lower() in IMAGE_EXTS:
        size_mb = path.stat().st_size / 1024 / 1024 if path.exists() else 0
        return f"type=photo size={size_mb:.1f}MB ext={path.suffix.lower()}"
    data = _run_ffprobe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    width = video.get("width")
    height = video.get("height")
    resolution = f"{width}x{height}" if width and height else "unknown"
    size_mb = path.stat().st_size / 1024 / 1024 if path.exists() else 0
    return (
        f"resolution={resolution} "
        f"video={video.get('codec_name', 'unknown')} "
        f"audio={audio.get('codec_name', 'unknown')} "
        f"size={size_mb:.1f}MB"
    )


def _ensure_ytdlp_bin() -> str:
    bin_path = Path(settings.ytdlp_bin)
    if bin_path.exists():
        return str(bin_path)
    return "yt-dlp"


def _check_ytdlp_bin() -> None:
    bin_path = _ensure_ytdlp_bin()
    proc = subprocess.run([bin_path, "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        log.warning("yt-dlp binary check failed: %s", proc.stdout.strip())


def _ytdlp_opts_for_url(url: str, workdir: Path) -> dict[str, Any]:
    opts = _base_opts(workdir)
    cookie_file = _cookie_file_for_url(url)
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    else:
        opts.pop("cookiefile", None)
    return opts


def _download_ytdlp_video(url: str, workdir: Path, fmt: str, *, merge: bool = False, codec_mode: str = "mp4", max_duration_seconds: int | None = None, progress_hook: Callable[[dict[str, Any]], None] | None = None, preflight_info: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any], Path | None]:
    _check_ytdlp_bin()
    opts = _ytdlp_opts_for_url(url, workdir)
    opts.update(
        {
            "format": fmt,
            "format_sort": ["res", "+br", "+size", "+fps", "vcodec:h264", "acodec:aac"],
            "max_filesize": settings.max_file_bytes,
            "remote_components": ["ejs:github"],
            "extractor_args": _youtube_extractor_args(),
            "writethumbnail": True,
            "convertthumbnails": "jpg",
            "embedsubs": False,
            "embed_chapters": True,
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "progress_hooks": [progress_hook] if progress_hook else [],
        }
    )
    if merge:
        opts.update(
            {
                "merge_output_format": _merge_output_format_for_mode(codec_mode),
                "postprocessors": [
                    {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                    {"key": "FFmpegMetadata"},
                ],
                "postprocessor_args": {"ffmpeg": ["-movflags", "+faststart"]},
            }
        )
    with YoutubeDL(opts) as ydl:
        info = preflight_info or ydl.extract_info(url, download=False)
        _validate_info(info, max_duration_seconds=max_duration_seconds)
        ydl.download([url])
    found = _find_file(workdir, _video_suffixes_for_mode(codec_mode))
    if not found:
        raise DownloadFailed("MP4 файл не найден после загрузки.")
    if not _filesize_ok(found):
        raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
    thumbnail = _find_ytdlp_thumbnail(workdir, found)
    return found, info, thumbnail


def _gallery_dl_config() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    extractor: dict[str, Any] = {
        "base-directory": str(DOWNLOAD_DIR),
        "archive": None,
        "skip": True,
        "sleep-request": "1.0-3.0",
    }
    for platform in ("instagram", "tiktok", "vk", "twitter"):
        cookie_file = COOKIES_DIR / f"{platform}.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            extractor[platform] = {"cookies": str(cookie_file)}
    if "twitter" in extractor:
        extractor["x"] = extractor["twitter"]
    config = {
        "extractor": extractor,
        "output": {"mode": "terminal", "progress": False},
    }
    GALLERY_DL_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _download_gallery_dl(url: str, workdir: Path) -> tuple[Path, dict[str, Any]]:
    _gallery_dl_config()
    cmd = [
        "gallery-dl",
        "--config",
        str(GALLERY_DL_CONFIG),
        "--directory",
        str(workdir),
        "--no-part",
        "--write-metadata",
        url,
    ]
    cookie_file = _cookie_file_for_url(url)
    if cookie_file:
        cmd[1:1] = ["--cookies", str(cookie_file)]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=settings.download_timeout_seconds)
    if proc.returncode != 0:
        raise DownloadFailed(f"gallery-dl не смог скачать медиа: {proc.stdout.strip()[-500:]}")
    found = _find_gallery_media(workdir)
    if not found:
        raise DownloadFailed("gallery-dl не создал медиа-файл.")
    if not _filesize_ok(found):
        raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
    info_file = _find_file(workdir, {".json"})
    info: dict[str, Any] = {}
    if info_file:
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            info = {"title": data.get("title") or data.get("description") or data.get("caption") or data.get("content") or ""}
        except Exception:
            log.debug("failed to parse gallery-dl metadata", exc_info=True)
    return found, info


def _http_get_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(1_000_000).decode("utf-8", "ignore")
    except (TimeoutError, socket.timeout, OSError) as exc:
        raise DownloadFailed(f"Не удалось получить страницу: {exc}") from exc


def _meta_content(page: str, *, property_name: str | None = None, name: str | None = None) -> str | None:
    attr = "property" if property_name else "name"
    value = property_name or name
    assert value is not None
    escaped = re.escape(value)
    patterns = [
        f"<meta[^>]+{attr}=[\"']{escaped}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        f"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+{attr}=[\"']{escaped}[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    return None


def _download_direct_file(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": "https://www.instagram.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as file:
            total = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_file_bytes:
                    raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
                file.write(chunk)
    except (TimeoutError, socket.timeout, OSError) as exc:
        raise DownloadFailed(f"Не удалось скачать файл: {exc}") from exc


def _download_og_media(url: str, workdir: Path) -> tuple[Path, dict[str, Any]]:
    page = _http_get_text(url)
    media_url = (
        _meta_content(page, property_name="og:video")
        or _meta_content(page, property_name="og:video:url")
        or _meta_content(page, property_name="og:image")
        or _meta_content(page, name="twitter:image")
    )
    if not media_url:
        raise DownloadFailed("Страница не отдала og:image/og:video.")
    title = _meta_content(page, property_name="og:title") or ""
    description = _meta_content(page, property_name="og:description") or ""
    suffix = ".jpg"
    clean_url = media_url.split("?", 1)[0].lower()
    for ext in MEDIA_EXTS:
        if clean_url.endswith(ext):
            suffix = ".jpg" if ext == ".jpeg" else ext
            break
    target = workdir / f"og-media{suffix}"
    _download_direct_file(media_url, target)
    if not _filesize_ok(target):
        raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
    return target, {"title": title, "description": description}




def _find_ytdlp_thumbnail(workdir: Path, video_path: Path) -> Path | None:
    candidates = [
        path
        for path in workdir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS and path != video_path
    ]
    if not candidates:
        return None
    jpg_candidates = [path for path in candidates if path.suffix.lower() in {".jpg", ".jpeg"}]
    return max(jpg_candidates or candidates, key=lambda path: path.stat().st_size)


def _move_thumbnail(path: Path | None, job_id: str) -> Path | None:
    if not path:
        return None
    final = DOWNLOAD_DIR / f"{job_id}.thumb{path.suffix.lower()}"
    shutil.move(str(path), final)
    return final



def _extract_frame_cover(video_path: Path, job_id: str) -> Path | None:
    """Extract first frame from video as cover using ffmpeg (fast, no re-encode)."""
    cover_path = DOWNLOAD_DIR / f"{job_id}.cover.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", str(video_path), "-vframes", "1", "-q:v", "2", str(cover_path)],
            capture_output=True, timeout=60, check=True,
        )
        if cover_path.exists() and cover_path.stat().st_size > 0:
            return cover_path
        log.warning("extracted frame cover empty for %s", job_id)
    except Exception as exc:
        log.warning("failed to extract frame cover for %s: %s", job_id, exc)
    return None

def _move_final(path: Path, job_id: str) -> Path:
    final = DOWNLOAD_DIR / f"{job_id}{path.suffix.lower()}"
    shutil.move(str(path), final)
    return final


def _move_gallery_finals(paths: list[Path], job_id: str) -> list[Path]:
    finals: list[Path] = []
    for index, path in enumerate(paths, start=1):
        final = DOWNLOAD_DIR / f"{job_id}-{index:02d}{path.suffix.lower()}"
        shutil.move(str(path), final)
        finals.append(final)
    return finals


def _media_type(path: Path) -> Literal["photo", "video", "document"]:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "photo"
    if suffix == ".mp4":
        return "video"
    return "document"


def _clear_workdir(workdir: Path) -> None:
    for child in workdir.rglob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)


def _try_gallery_dl(url: str, workdir: Path, job_id: str) -> DownloadResult:
    _clear_workdir(workdir)
    _, info = _download_gallery_dl(url, workdir)
    media_files = _find_gallery_media_files(workdir)
    if not media_files:
        raise DownloadFailed("gallery-dl не создал медиа-файл.")
    finals = _move_gallery_finals(media_files, job_id)
    caption = _caption_from_info(info)
    items = tuple(
        DownloadItem(path=final, media_type=_media_type(final), caption=caption if index == 0 else None)
        for index, final in enumerate(finals)
    )
    first = items[0]
    result = DownloadResult(
        path=first.path,
        media_type=first.media_type,
        caption=first.caption,
        extractor="gallery-dl",
        items=items,
    )
    log.info("downloaded job %s via gallery-dl: files=%s first_type=%s", job_id, len(items), result.media_type)
    return result


def _try_og_media(url: str, workdir: Path, job_id: str) -> DownloadResult:
    _clear_workdir(workdir)
    path, info = _download_og_media(url, workdir)
    final = _move_final(path, job_id)
    result = DownloadResult(path=final, media_type=_media_type(final), caption=_caption_from_info(info), extractor="og-meta")
    log.info("downloaded job %s via og-meta: type=%s path=%s", job_id, result.media_type, final)
    return result


def _download_with_fallbacks(url: str, job_id: str, max_duration_seconds: int | None = None, progress_hook: Callable[[dict[str, Any]], None] | None = None) -> DownloadResult:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    workdir = DOWNLOAD_DIR / job_id
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    formats: list[tuple[str, bool, str, dict[str, Any] | None]] = []
    last_error: Exception | None = None
    platform = _host_platform(url)
    if platform == "youtube":
        opts = _ytdlp_opts_for_url(url, workdir)
        opts.update({"quiet": True, "remote_components": ["ejs:github"], "extractor_args": _youtube_extractor_args()})
        with YoutubeDL(opts) as ydl:
            preflight_info = ydl.extract_info(url, download=False)
        _validate_info(preflight_info, max_duration_seconds=max_duration_seconds)
        selected_fmt, selected_merge, selected_mode = _select_safe_telegram_format(preflight_info, settings.max_file_bytes)
        formats.append((selected_fmt, selected_merge, selected_mode, preflight_info))
    else:
        formats.extend([
            ("best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=480]/best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=360]/best[ext=mp4][vcodec^=avc1][acodec^=mp4a]", False, "mp4", None),
            ("worst[ext=mp4][vcodec!=none][acodec!=none]/best[ext=mp4][vcodec!=none][acodec!=none][height<=480]/best[ext=mp4][vcodec!=none][acodec!=none]", False, "mp4", None),
            ("worstvideo[ext=mp4][vcodec^=avc1][height<=480]+worstaudio[ext=m4a]/bestvideo[ext=mp4][vcodec^=avc1][height<=480]+bestaudio[ext=m4a]/bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]", True, "mp4", None),
            ("best[ext=webm][vcodec^=vp9][height<=1080]/best[ext=webm][vcodec^=av1][height<=1080]/best[ext=webm]", True, "mp4", None),
            ("worst[ext=mp4]/best[ext=mp4][height<=480]/best[ext=mp4]/best", False, "mp4", None),
        ])
    prefer_gallery = platform == "instagram" and any(marker in url for marker in ("/p/", "/reel/", "/reels/", "/stories/"))
    try:
        if prefer_gallery:
            try:
                return _try_gallery_dl(url, workdir, job_id)
            except (DownloadFailed, DownloadRejected, subprocess.TimeoutExpired) as exc:
                last_error = exc
                log.info("gallery-dl first attempt failed for %s: %s", job_id, exc)

        for fmt, merge, codec_mode, preflight_info in formats:
            _clear_workdir(workdir)
            try:
                path, info, thumbnail_path = _download_ytdlp_video(url, workdir, fmt, merge=merge, codec_mode=codec_mode, max_duration_seconds=max_duration_seconds, progress_hook=progress_hook, preflight_info=preflight_info)
                if not _is_supported_video_file(path, codec_mode):
                    raise DownloadFailed(f"Получился неподходящий файл для режима {codec_mode}: {path.suffix}")
                if codec_mode == "mp4" and not _is_telegram_mp4(path):
                    log.warning("%s is not confirmed h264+aac; accepting as video fallback", path)
                final = _move_final(path, job_id)
                frame_cover = _extract_frame_cover(final, job_id)
                cover = frame_cover or _move_thumbnail(thumbnail_path, job_id)
                log.info("downloaded job %s via yt-dlp: type=video path=%s cover=%s", job_id, final, bool(cover))
                return DownloadResult(
                    path=final,
                    media_type="video",
                    caption=_caption_from_info(info),
                    extractor="yt-dlp",
                    items=(DownloadItem(final, "video", _caption_from_info(info), cover_path=cover, width=info.get("width"), height=info.get("height"), duration=info.get("duration")),),
                )
            except DownloadRejected as exc:
                log.info("yt-dlp video rejected for %s: %s", job_id, exc)
                raise
            except (DownloadError, DownloadFailed, OSError) as exc:
                last_error = exc
                log.info("yt-dlp video attempt failed for %s: %s", job_id, exc)

        if platform == "youtube":
            raise DownloadFailed(str(last_error) if last_error else "yt-dlp не смог скачать YouTube-видео.")

        if not prefer_gallery:
            try:
                return _try_gallery_dl(url, workdir, job_id)
            except (DownloadFailed, DownloadRejected, subprocess.TimeoutExpired) as exc:
                last_error = exc
                log.info("gallery-dl fallback failed for %s: %s", job_id, exc)

        try:
            return _try_og_media(url, workdir, job_id)
        except (DownloadFailed, DownloadRejected) as exc:
            last_error = exc
            log.info("og fallback failed for %s: %s", job_id, exc)

        if isinstance(last_error, DownloadRejected):
            raise last_error
        raise DownloadFailed(str(last_error) if last_error else "Не удалось скачать медиа.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def download_media(url: str, job_id: str, *, max_duration_seconds: int | None = None, progress_hook: Callable[[dict[str, Any]], None] | None = None, timeout_seconds: int | None = None) -> DownloadResult:
    return await asyncio.wait_for(
        asyncio.to_thread(_download_with_fallbacks, url, job_id, max_duration_seconds, progress_hook),
        timeout=timeout_seconds if timeout_seconds is not None else settings.download_timeout_seconds,
    )


def cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("failed to cleanup %s", path, exc_info=True)
