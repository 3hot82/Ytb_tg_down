from __future__ import annotations

import asyncio
import logging
import subprocess
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
from .redis_keys import ACTIVE_JOBS, CHAT_LOCK_PREFIX, JOB_PREFIX, MEDIA_CACHE_PREFIX, PAUSE_FLAG

log = logging.getLogger(__name__)


def _bot_session() -> AiohttpSession | None:
    if not settings.telegram_api_base_url:
        return None
    return AiohttpSession(
        api=TelegramAPIServer(
            base=settings.telegram_api_base_url,
            file=settings.telegram_api_file_url,
            is_local=True,
        )
    )




async def _release(redis: Redis, job: MediaJob) -> None:
    lock_key = f"{CHAT_LOCK_PREFIX}{job.chat_id}"
    current = await redis.get(lock_key)
    if current == job.id:
        await redis.delete(lock_key)
    await redis.delete(f"{JOB_PREFIX}{job.id}")


async def _delete_message(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        log.debug("failed to delete message chat=%s message=%s", chat_id, message_id, exc_info=True)


async def _send_error(bot: Bot, job: MediaJob, text: str) -> None:
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


async def process_job(redis: Redis, bot: Bot, job: MediaJob) -> None:
    await redis.hset(ACTIVE_JOBS, job.id, str(job.chat_id))
    result = None
    try:
        if await _try_send_cached(redis, bot, job):
            return
        result = await download_media(job.url, job.id)
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
                    )
                    await _cache_sent_message(redis, job, "video", sent, item.caption, enabled=cache_enabled)
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
        await redis.hdel(ACTIVE_JOBS, job.id)
        await _release(redis, job)


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token, session=_bot_session())
    try:
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
