from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, Message
from redis.asyncio import Redis

from .config import settings
from .downloader import DownloadItem, DownloadRejected, cleanup_file, describe_media, download_media
from .i18n import detect_language, t
from .models import MediaJob
from .redis_keys import (
    ACTIVE_JOBS,
    JOB_PREFIX,
    MEDIA_CACHE_PREFIX,
    PAUSE_FLAG,
    PENDING_JOBS_CHAT_PREFIX,
    PENDING_JOBS_USER_PREFIX,
    URL_INFLIGHT_PREFIX,
    URL_WAITERS_PREFIX,
    USER_LANG_PREFIX,
)

log = logging.getLogger(__name__)


def _bot_session() -> AiohttpSession:
    if not settings.telegram_api_base_url:
        return AiohttpSession(timeout=settings.telegram_request_timeout_seconds)
    return AiohttpSession(
        timeout=settings.telegram_request_timeout_seconds,
        api=TelegramAPIServer(
            base=settings.telegram_api_base_url,
            file=settings.telegram_api_file_url,
            is_local=True,
        )
    )




async def _release(redis: Redis, job: MediaJob) -> None:
    if job.user_id is not None:
        await redis.delete(f"{JOB_PREFIX}{job.user_id}")


async def _delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).lower():
            log.debug("failed to delete message chat=%s message=%s: %s", chat_id, message_id, exc)
    except TelegramForbiddenError:
        log.debug("forbidden to delete message chat=%s message=%s", chat_id, message_id)


def _progress_target(job: MediaJob) -> tuple[int, int] | None:
    if job.progress_chat_id and job.progress_message_id:
        return job.progress_chat_id, job.progress_message_id
    if job.status_message_id:
        return job.chat_id, job.status_message_id
    return None


async def _set_progress(bot: Bot, job: MediaJob, text: str) -> None:
    target = _progress_target(job)
    if not target:
        return
    chat_id, message_id = target
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            log.debug("failed to edit progress chat=%s message=%s", chat_id, message_id, exc_info=True)
    except TelegramForbiddenError:
        log.debug("failed to edit progress chat=%s message=%s", chat_id, message_id, exc_info=True)


async def _send_error(bot: Bot, job: MediaJob, text: str) -> None:
    if _progress_target(job):
        await _set_progress(bot, job, f"❌ {text}")
        return
    msg = await bot.send_message(job.chat_id, text, reply_to_message_id=job.message_id)
    await asyncio.sleep(10)
    await _delete_message(bot, job.chat_id, msg.message_id)




def _make_video_thumbnail(video_path: Path) -> Path | None:
    thumb_path = video_path.with_suffix(".thumb.jpg")
    for timestamp in ("00:00:03", "00:00:01", "00:00:00.5"):
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                timestamp,
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                "-q:v",
                "3",
                str(thumb_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path
    thumb_path.unlink(missing_ok=True)
    return None




def _media_cache_key(url: str) -> str:
    audio_part = "default"
    if settings.youtube_multi_audio and settings.youtube_audio_language:
        audio_part = f"lang:{settings.youtube_audio_language}"
    return f"{MEDIA_CACHE_PREFIX}{settings.video_codec_mode}:{audio_part}:{url}"


def _legacy_media_cache_key(url: str) -> str:
    return f"{MEDIA_CACHE_PREFIX}{url}"


def _format_caption_for_chat(item: DownloadItem, chat_id: int, bot_username: str | None = None, lang: str = "ru") -> str | None:
    is_group = chat_id < 0
    # In group chats: short caption (title only + promo)
    # In private chats: full caption (title + chapters + sponsorblock + promo)
    base = (item.short_caption if is_group else item.full_caption) or item.caption
    if not base:
        return None
    base = re.sub(r'<[^>]+>', '', base).strip()
    promo_text = t("caption.download_via", lang, bot_username=bot_username) if bot_username else ""
    promo = f"\n\n{promo_text}" if promo_text else ""
    caption = f"{base}{promo}"
    if len(caption) > 1024:
        avail = 1024 - len(promo) - 3
        if avail > 0 and len(base) > avail:
            caption = f"{base[:avail].rstrip()}…{promo}"
        else:
            caption = caption[:1021].rstrip() + "…"
    return caption


async def _try_send_cached(redis: Redis, bot: Bot, job: MediaJob, bot_username: str | None = None, lang: str = "ru") -> bool:
    cache_key = _media_cache_key(job.url)
    legacy_key = _legacy_media_cache_key(job.url)
    if job.force_download:
        await redis.delete(cache_key, legacy_key)
        log.info("job %s bypassed and cleared telegram file_id cache url=%s", job.id, job.url)
        return False
    raw = await redis.get(cache_key)
    if not raw:
        return False

    is_group = job.chat_id < 0
    try:
        data = json.loads(raw)
        media_type = data.get("media_type")
        file_id = data.get("file_id")
        base_caption = (data.get("short_caption") if is_group else data.get("full_caption")) or data.get("full_caption") or data.get("short_caption") or data.get("title")
    except Exception:
        # Fallback for old tab-separated format
        media_type, file_id, base_caption = (raw.split("\t", 2) + [None, None, None])[:3]

    if media_type not in {"photo", "video", "document"} or not file_id:
        await redis.delete(cache_key)
        return False

    promo_text = t("caption.download_via", lang, bot_username=bot_username) if bot_username else ""
    promo = f"\n\n{promo_text}" if promo_text else ""
    if base_caption:
        base_caption = re.sub(r'<[^>]+>', '', base_caption).strip()
        caption = f"{base_caption}{promo}"
        if len(caption) > 1024:
            avail = 1024 - len(promo) - 3
            if avail > 0 and len(base_caption) > avail:
                caption = f"{base_caption[:avail].rstrip()}…{promo}"
            else:
                caption = caption[:1021].rstrip() + "…"
    else:
        caption = None

    try:
        if media_type == "photo":
            await bot.send_photo(job.chat_id, file_id, caption=caption, reply_to_message_id=job.message_id)
        elif media_type == "video":
            await bot.send_video(job.chat_id, file_id, caption=caption, reply_to_message_id=job.message_id, supports_streaming=True)
        else:
            await bot.send_document(job.chat_id, file_id, caption=caption, reply_to_message_id=job.message_id)
        log.info("job %s sent from telegram file_id cache url=%s type=%s is_group=%s lang=%s", job.id, job.url, media_type, is_group, lang)
        return True
    except TelegramBadRequest as exc:
        await redis.delete(cache_key)
        log.warning("telegram file_id cache failed for %s, invalidating: %s", job.url, exc)
        return False


def _message_file_id(message: Message, media_type: str) -> str | None:
    if media_type == "photo" and message.photo:
        return message.photo[-1].file_id
    if media_type == "video" and message.video:
        return message.video.file_id
    if message.document:
        return message.document.file_id
    return None


async def _cache_sent_message(
    redis: Redis,
    job: MediaJob,
    media_type: str,
    message: Message,
    item: DownloadItem,
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    file_id = _message_file_id(message, media_type)
    if not file_id:
        log.warning("job %s sent but no file_id returned for cache", job.id)
        return
    payload = {
        "media_type": media_type,
        "file_id": file_id,
        "title": item.title,
        "short_caption": item.short_caption,
        "full_caption": item.full_caption or item.caption,
    }
    await redis.set(
        _media_cache_key(job.url),
        json.dumps(payload, ensure_ascii=False),
        ex=settings.media_cache_ttl_seconds,
    )
    log.info("cached telegram file_id for url=%s type=%s ttl=%s", job.url, media_type, settings.media_cache_ttl_seconds)




async def _release_pending(redis: Redis, job: MediaJob) -> None:
    chat_key = f"{PENDING_JOBS_CHAT_PREFIX}{job.chat_id}"
    pipe = redis.pipeline()
    pipe.srem(chat_key, job.id)
    if job.user_id is not None:
        pipe.srem(f"{PENDING_JOBS_USER_PREFIX}{job.user_id}", job.id)
    await pipe.execute()


async def process_job(redis: Redis, bot: Bot, job: MediaJob, bot_username: str | None = None) -> None:
    await redis.hset(ACTIVE_JOBS, job.id, time.time())
    result = None
    loop = asyncio.get_running_loop()
    last_progress = 0.0

    # Determine user/chat language from Redis (default 'ru' for CIS, 'en' for others)
    lang = "ru"
    if job.user_id:
        raw_lang = await redis.get(f"{USER_LANG_PREFIX}{job.user_id}")
        if raw_lang:
            lang = raw_lang.decode("utf-8") if isinstance(raw_lang, bytes) else raw_lang
    elif job.chat_id:
        raw_lang = await redis.get(f"{USER_LANG_PREFIX}{job.chat_id}")
        if raw_lang:
            lang = raw_lang.decode("utf-8") if isinstance(raw_lang, bytes) else raw_lang

    def progress_hook(data: dict) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress < 3:
            return
        last_progress = now
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        downloaded = data.get("downloaded_bytes") or 0
        status = data.get("status")
        if status == "downloading" and total:
            percent = min(downloaded / total * 100, 100)
            downloaded_mb = downloaded / 1024 / 1024
            total_mb = total / 1024 / 1024
            text = f"⬇️ Скачиваю: {percent:.1f}% ({downloaded_mb:.0f}/{total_mb:.0f} MB)" if lang == "ru" else f"⬇️ Downloading: {percent:.1f}% ({downloaded_mb:.0f}/{total_mb:.0f} MB)"
        elif status == "finished":
            text = "⚙️ Обрабатываю видео…" if lang == "ru" else "⚙️ Processing media…"
        else:
            return
        loop.call_soon_threadsafe(lambda: asyncio.create_task(_set_progress(bot, job, text)))

    try:
        if await _try_send_cached(redis, bot, job, bot_username=bot_username, lang=lang):
            await _set_progress(bot, job, "✅ Отправил из кэша" if lang == "ru" else "✅ Sent from cache")
            return
        is_admin = job.user_id is not None and job.user_id in settings.admin_ids
        max_duration = 0 if is_admin else settings.max_duration_seconds
        timeout_seconds = None if is_admin else settings.download_timeout_seconds
        await _set_progress(bot, job, "🔎 Проверяю ссылку…" if lang == "ru" else "🔎 Checking link…")
        result = await download_media(
            job.url,
            job.id,
            max_duration_seconds=max_duration,
            progress_hook=progress_hook,
            timeout_seconds=timeout_seconds,
        )
        await _set_progress(bot, job, "📤 Отправляю видео…" if lang == "ru" else "📤 Uploading media…")
        media_info = describe_media(result.path)
        items = result.all_items()
        cache_enabled = len(items) == 1
        log.info("job %s downloaded: url=%s type=%s caption=%r items=%s cache=%s %s", job.id, job.url, result.media_type, result.caption, len(items), cache_enabled, media_info)
        for item in items:
            if item.path.stat().st_size > settings.max_file_bytes:
                raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
            caption = _format_caption_for_chat(item, job.chat_id, bot_username=bot_username, lang=lang)
            if item.media_type == "photo":
                sent = await bot.send_photo(
                    chat_id=job.chat_id,
                    photo=FSInputFile(item.path),
                    caption=caption,
                    reply_to_message_id=job.message_id,
                )
                await _cache_sent_message(redis, job, "photo", sent, item, enabled=cache_enabled)
                await _set_progress(bot, job, "✅ Фото отправлено" if lang == "ru" else "✅ Photo sent")
            elif item.media_type == "video":
                generated_thumbnail = None
                thumbnail = item.thumbnail_path
                if not thumbnail or not thumbnail.exists():
                    generated_thumbnail = _make_video_thumbnail(item.path)
                    thumbnail = generated_thumbnail
                cover = item.cover_path
                try:
                    sent = await bot.send_video(
                        chat_id=job.chat_id,
                        video=FSInputFile(item.path),
                        thumbnail=FSInputFile(thumbnail) if thumbnail and thumbnail.exists() else None,
                        cover=FSInputFile(cover) if cover and cover.exists() else None,
                        caption=caption,
                        reply_to_message_id=job.message_id,
                        supports_streaming=True,
                        duration=item.duration,
                        width=item.width,
                        height=item.height,
                    )
                    await _cache_sent_message(redis, job, "video", sent, item, enabled=cache_enabled)
                    await _set_progress(bot, job, "✅ Видео отправлено" if lang == "ru" else "✅ Video sent")
                finally:
                    if generated_thumbnail:
                        generated_thumbnail.unlink(missing_ok=True)
            else:
                sent = await bot.send_document(
                    chat_id=job.chat_id,
                    document=FSInputFile(item.path),
                    caption=caption,
                    reply_to_message_id=job.message_id,
                )
                await _cache_sent_message(redis, job, "document", sent, item, enabled=cache_enabled)
                await _set_progress(bot, job, "✅ Файл отправлен" if lang == "ru" else "✅ File sent")
    except DownloadRejected as exc:
        await _send_error(bot, job, f"Не могу скачать: {exc}")
    except asyncio.TimeoutError:
        await _send_error(bot, job, "Загрузка заняла слишком много времени и остановлена.")
        log.exception("job timeout: %s", job.id)
    except Exception as exc:  # noqa: BLE001 - worker must survive bad URLs/sites
        await _send_error(bot, job, "Не удалось скачать это медиа.")
        log.exception("job failed %s: %s", job.id, exc)
    finally:
        if result:
            for item in result.all_items():
                cleanup_file(item.path)
                if item.thumbnail_path:
                    cleanup_file(item.thumbnail_path)
                if item.cover_path:
                    cleanup_file(item.cover_path)
        waiter_key = f"{URL_WAITERS_PREFIX}{job.url}"
        waiter_raws = await redis.smembers(waiter_key)
        if waiter_raws:
            cache_raw = await redis.get(f"{MEDIA_CACHE_PREFIX}{job.url}")
            if cache_raw:
                try:
                    cdata = json.loads(cache_raw)
                    media_type = cdata.get("media_type")
                    file_id = cdata.get("file_id")
                except Exception:
                    media_type, file_id, _ = (cache_raw.split("\t", 2) + [None, None, None])[:3]
                if media_type in {"photo", "video", "document"} and file_id:
                    for waiter_raw in waiter_raws:
                        try:
                            waiter = json.loads(waiter_raw)
                            w_chat_id = waiter.get("chat_id")
                            if w_chat_id:
                                w_is_group = w_chat_id < 0
                                try:
                                    cdata = json.loads(cache_raw)
                                    base_c = (cdata.get("short_caption") if w_is_group else cdata.get("full_caption")) or cdata.get("full_caption") or cdata.get("short_caption") or cdata.get("title")
                                except Exception:
                                    base_c = None
                                promo = f"\n\n📥 @{bot_username}" if bot_username else ""
                                w_caption = f"{base_c}{promo}" if base_c else None
                                if media_type == "photo":
                                    await bot.send_photo(w_chat_id, file_id, caption=w_caption, reply_to_message_id=waiter.get("message_id"))
                                elif media_type == "video":
                                    await bot.send_video(w_chat_id, file_id, caption=w_caption, reply_to_message_id=waiter.get("message_id"), supports_streaming=True)
                                else:
                                    await bot.send_document(w_chat_id, file_id, caption=w_caption, reply_to_message_id=waiter.get("message_id"))
                                log.info("sent cached to waiter chat=%s url=%s", w_chat_id, job.url)
                        except TelegramBadRequest as exc:
                            log.warning("failed to send cached to waiter chat=%s url=%s: %s", waiter.get("chat_id"), job.url, exc)
            await redis.delete(waiter_key)
        await redis.hdel(ACTIVE_JOBS, job.id)
        await redis.delete(f"{URL_INFLIGHT_PREFIX}{job.url}")
        await _release_pending(redis, job)
        await _release(redis, job)


async def _clear_startup_queue(redis: Redis) -> None:
    if not settings.clear_queue_on_worker_start:
        return
    queued = await redis.llen(settings.queue_name)
    pending_chat_keys = [key async for key in redis.scan_iter(f"{PENDING_JOBS_CHAT_PREFIX}*")]
    pending_user_keys = [key async for key in redis.scan_iter(f"{PENDING_JOBS_USER_PREFIX}*")]
    inflight_keys = [key async for key in redis.scan_iter(f"{URL_INFLIGHT_PREFIX}*")]
    waiter_keys = [key async for key in redis.scan_iter(f"{URL_WAITERS_PREFIX}*")]
    pipe = redis.pipeline()
    pipe.delete(settings.queue_name, ACTIVE_JOBS)
    for key in pending_chat_keys + pending_user_keys + inflight_keys + waiter_keys:
        pipe.delete(key)
    await pipe.execute()
    log.info(
        "worker startup queue guard cleared queued=%s pending_chat=%s pending_user=%s inflight=%s waiters=%s",
        queued,
        len(pending_chat_keys),
        len(pending_user_keys),
        len(inflight_keys),
        len(waiter_keys),
    )


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token, session=_bot_session())
    bot_username: str | None = None
    try:
        me = await bot.get_me()
        bot_username = me.username
        log.info("worker running for bot @%s", bot_username)
    except Exception as exc:
        log.warning("failed to fetch bot username: %s", exc)
    try:
        await _clear_startup_queue(redis)
        while True:
            if await redis.get(PAUSE_FLAG):
                await asyncio.sleep(2)
                continue
            item = await redis.blpop(settings.queue_name, timeout=5)
            if not item:
                continue
            _, raw = item
            try:
                job = MediaJob.loads(raw)
            except Exception:
                log.exception("bad job payload: %r", raw)
                continue
            await process_job(redis, bot, job, bot_username=bot_username)
    finally:
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
