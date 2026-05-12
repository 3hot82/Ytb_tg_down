from __future__ import annotations

import asyncio
import json
import logging
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
from .downloader import DownloadRejected, cleanup_file, describe_media, download_media
from .models import MediaJob
from .redis_keys import ACTIVE_JOBS, JOB_PREFIX, MEDIA_CACHE_PREFIX, PAUSE_FLAG, PENDING_JOBS_CHAT_PREFIX, PENDING_JOBS_USER_PREFIX, URL_INFLIGHT_PREFIX, URL_WAITERS_PREFIX

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
    # Bot no longer uses CHAT_LOCK; only cleanup JOB_PREFIX.
    await redis.delete(f"{JOB_PREFIX}{job.id}")


async def _delete_message(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        log.debug("failed to delete message chat=%s message=%s", chat_id, message_id, exc_info=True)


def _progress_target(job: MediaJob) -> tuple[int, int] | None:
    if job.progress_chat_id is None or job.progress_message_id is None:
        return None
    return job.progress_chat_id, job.progress_message_id


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
                "scale='min(320,iw)':-2",
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




async def _try_send_cached(redis: Redis, bot: Bot, job: MediaJob) -> bool:
    if job.force_download:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{job.url}")
        log.info("job %s bypassed and cleared telegram file_id cache url=%s", job.id, job.url)
        return False
    raw = await redis.get(f"{MEDIA_CACHE_PREFIX}{job.url}")
    if not raw:
        return False
    media_type, file_id, caption = (raw.split("\t", 2) + [None, None, None])[:3]
    if media_type not in {"photo", "video", "document"}:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{job.url}")
        return False
    if not file_id:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{job.url}")
        return False
    try:
        if media_type == "photo":
            await bot.send_photo(job.chat_id, file_id, caption=caption or None, reply_to_message_id=job.message_id)
        elif media_type == "video":
            await bot.send_video(job.chat_id, file_id, caption=caption or None, reply_to_message_id=job.message_id, supports_streaming=True)
        else:
            await bot.send_document(job.chat_id, file_id, caption=caption or None, reply_to_message_id=job.message_id)
        log.info("job %s sent from telegram file_id cache url=%s type=%s", job.id, job.url, media_type)
        return True
    except TelegramBadRequest as exc:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{job.url}")
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
    caption: str | None,
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    file_id = _message_file_id(message, media_type)
    if not file_id:
        log.warning("job %s sent but no file_id returned for cache", job.id)
        return
    safe_caption = (caption or "").replace("\t", " ").replace("\n", " ")
    await redis.set(
        f"{MEDIA_CACHE_PREFIX}{job.url}",
        f"{media_type}\t{file_id}\t{safe_caption}",
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


async def process_job(redis: Redis, bot: Bot, job: MediaJob) -> None:
    await redis.hset(ACTIVE_JOBS, job.id, str(job.chat_id))
    await redis.expire(ACTIVE_JOBS, settings.job_ttl_seconds)
    result = None
    loop = asyncio.get_running_loop()
    last_progress = 0.0

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
            text = f"⬇️ Скачиваю: {percent:.1f}% ({downloaded_mb:.0f}/{total_mb:.0f} MB)"
        elif status == "finished":
            text = "⚙️ Обрабатываю видео…"
        else:
            return
        loop.call_soon_threadsafe(lambda: asyncio.create_task(_set_progress(bot, job, text)))

    try:
        if await _try_send_cached(redis, bot, job):
            await _set_progress(bot, job, "✅ Отправил из кэша")
            return
        is_admin = job.user_id is not None and job.user_id in settings.admin_ids
        max_duration = 0 if is_admin else settings.max_duration_seconds
        timeout_seconds = None if is_admin else settings.download_timeout_seconds
        await _set_progress(bot, job, "🔎 Проверяю ссылку…")
        result = await download_media(
            job.url,
            job.id,
            max_duration_seconds=max_duration,
            progress_hook=progress_hook,
            timeout_seconds=timeout_seconds,
        )
        await _set_progress(bot, job, "📤 Отправляю видео…")
        media_info = describe_media(result.path)
        items = result.all_items()
        cache_enabled = len(items) == 1
        log.info("job %s downloaded: url=%s type=%s caption=%r items=%s cache=%s %s", job.id, job.url, result.media_type, result.caption, len(items), cache_enabled, media_info)
        for item in items:
            if item.path.stat().st_size > settings.max_file_bytes:
                raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
            if item.media_type == "photo":
                sent = await bot.send_photo(
                    chat_id=job.chat_id,
                    photo=FSInputFile(item.path),
                    caption=item.caption,
                    reply_to_message_id=job.message_id,
                )
                await _cache_sent_message(redis, job, "photo", sent, item.caption, enabled=cache_enabled)
                await _set_progress(bot, job, "✅ Фото отправлено")
            elif item.media_type == "video":
                generated_thumbnail = _make_video_thumbnail(item.path)
                thumbnail = item.thumbnail_path or generated_thumbnail
                cover = item.cover_path
                try:
                    sent = await bot.send_video(
                        chat_id=job.chat_id,
                        video=FSInputFile(item.path),
                        thumbnail=FSInputFile(thumbnail) if thumbnail else None,
                        cover=FSInputFile(cover) if cover else None,
                        caption=item.caption,
                        reply_to_message_id=job.message_id,
                        supports_streaming=True,
                        duration=item.duration,
                    )
                    await _cache_sent_message(redis, job, "video", sent, item.caption, enabled=cache_enabled)
                    await _set_progress(bot, job, "✅ Видео отправлено")
                finally:
                    if generated_thumbnail:
                        generated_thumbnail.unlink(missing_ok=True)
            else:
                sent = await bot.send_document(
                    chat_id=job.chat_id,
                    document=FSInputFile(item.path),
                    caption=item.caption,
                    reply_to_message_id=job.message_id,
                )
                await _cache_sent_message(redis, job, "document", sent, item.caption, enabled=cache_enabled)
                await _set_progress(bot, job, "✅ Файл отправлен")
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
        # Send cached result to waiters who requested same URL while it was downloading
        waiter_key = f"{URL_WAITERS_PREFIX}{job.url}"
        waiter_raws = await redis.smembers(waiter_key)
        if waiter_raws:
            cache_raw = await redis.get(f"{MEDIA_CACHE_PREFIX}{job.url}")
            if cache_raw:
                media_type, file_id, _ = (cache_raw.split("\t", 2) + [None, None, None])[:3]
                if media_type in {"photo", "video", "document"} and file_id:
                    for waiter_raw in waiter_raws:
                        try:
                            waiter = json.loads(waiter_raw)
                            if media_type == "photo":
                                await bot.send_photo(waiter["chat_id"], file_id, reply_to_message_id=waiter.get("message_id"))
                            elif media_type == "video":
                                await bot.send_video(waiter["chat_id"], file_id, reply_to_message_id=waiter.get("message_id"), supports_streaming=True)
                            else:
                                await bot.send_document(waiter["chat_id"], file_id, reply_to_message_id=waiter.get("message_id"))
                            log.info("sent cached to waiter chat=%s url=%s", waiter["chat_id"], job.url)
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
            await process_job(redis, bot, job)
    finally:
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
