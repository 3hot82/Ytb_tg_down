from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile
from redis.asyncio import Redis

from .config import settings
from .downloader import DownloadRejected, cleanup_file, describe_media, download_media
from .models import MediaJob
from .redis_keys import ACTIVE_JOBS, CHAT_LOCK_PREFIX, JOB_PREFIX, PAUSE_FLAG

log = logging.getLogger(__name__)


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


async def process_job(redis: Redis, bot: Bot, job: MediaJob) -> None:
    await redis.hset(ACTIVE_JOBS, job.id, str(job.chat_id))
    result = None
    try:
        result = await download_media(job.url, job.id)
        media_info = describe_media(result.path)
        log.info("job %s downloaded: url=%s type=%s caption=%r %s", job.id, job.url, result.media_type, result.caption, media_info)
        if result.path.stat().st_size > settings.max_file_bytes:
            raise DownloadRejected(f"Файл больше {settings.max_file_mb} MB.")
        if result.media_type == "photo":
            await bot.send_photo(
                chat_id=job.chat_id,
                photo=FSInputFile(result.path),
                caption=result.caption,
                reply_to_message_id=job.message_id,
            )
        elif result.media_type == "video":
            await bot.send_video(
                chat_id=job.chat_id,
                video=FSInputFile(result.path),
                caption=result.caption,
                reply_to_message_id=job.message_id,
                supports_streaming=True,
            )
        else:
            await bot.send_document(
                chat_id=job.chat_id,
                document=FSInputFile(result.path),
                caption=result.caption,
                reply_to_message_id=job.message_id,
            )
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
            cleanup_file(result.path)
        await redis.hdel(ACTIVE_JOBS, job.id)
        await _release(redis, job)


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token)
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
