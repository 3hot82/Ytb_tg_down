from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from ..config import settings
from ..i18n import detect_language, t

log = logging.getLogger(__name__)

# Лимит: 5 запросов на скачивание в минуту на пользователя
MAX_REQUESTS = 5
WINDOW_SECONDS = 60

# In-memory хранилище: {user_id: [timestamp1, timestamp2, ...]}
_user_requests: dict[int, list[float]] = {}


def cleanup_stale_entries() -> int:
    """Удаляет устаревшие записи из in-memory хранилища."""
    now = time.monotonic()
    stale_users = [
        uid for uid, timestamps in _user_requests.items()
        if not any(now - ts < WINDOW_SECONDS for ts in timestamps)
    ]
    for uid in stale_users:
        del _user_requests[uid]
    if stale_users:
        log.debug("Rate limit: удалено %d устаревших записей", len(stale_users))
    return len(stale_users)


async def rate_limit_cleanup_loop(interval_seconds: int = 180) -> None:
    """Фоновая задача для периодической очистки памяти."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            cleanup_stale_entries()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("Ошибка в фоновой очистке rate limit: %s", exc)


class RateLimitMiddleware(BaseMiddleware):
    """In-memory rate limiting для запросов на скачивание медиа."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        text = event.text or event.caption
        if not text:
            return await handler(event, data)

        # Проверяем, содержит ли сообщение ссылки (не ограничиваем команды вроде /start, /help, /lang)
        if text.startswith(("/", "!", "⚙️", "🌐", "📊")):
            return await handler(event, data)

        user = event.from_user
        if not user:
            return await handler(event, data)

        user_id = user.id
        # Администраторы не ограничиваются лимитом
        if user_id in settings.admin_ids:
            return await handler(event, data)

        now = time.monotonic()

        # Очищаем старые временные метки для текущего пользователя
        if user_id in _user_requests:
            _user_requests[user_id] = [
                ts for ts in _user_requests[user_id]
                if now - ts < WINDOW_SECONDS
            ]
        else:
            _user_requests[user_id] = []

        # Проверяем лимит
        if len(_user_requests[user_id]) >= MAX_REQUESTS:
            oldest = _user_requests[user_id][0]
            wait_sec = max(1, int(WINDOW_SECONDS - (now - oldest)) + 1)
            lang = detect_language(getattr(user, "language_code", None))
            await event.reply(
                t("error.rate_limit", lang=lang, seconds=wait_sec),
            )
            log.info("Rate limit сработал для user_id=%s: подождать %s сек", user_id, wait_sec)
            return None

        # Записываем запрос и пропускаем дальше
        _user_requests[user_id].append(now)
        return await handler(event, data)
