from __future__ import annotations

import html
import logging
from typing import Any
from aiogram import Bot
from redis.asyncio import Redis

from .config import settings
from .database import log_error_to_db
from .i18n import t

log = logging.getLogger(__name__)

ALERT_COOLDOWN_PREFIX = "ytbot:alert:cooldown:"
ALERT_COOLDOWN_SECONDS = 300  # 5 minutes cooldown per critical error category

# ONLY notify admins for real server/infra emergencies:
CRITICAL_ADMIN_CATEGORIES = {"bot_detection", "rate_limit", "proxy_error"}


def classify_error(exc: Exception, url: str = "") -> tuple[str, str, str]:
    """
    Classify the error into (category, user_friendly_title, admin_recommendation).
    """
    err_str = str(exc).lower()

    if any(k in err_str for k in ("sign in to confirm", "bot detection", "confirm you're not a bot", "cookies", "login required")):
        return (
            "bot_detection",
            "🛡 Блокировка YouTube (Bot Detection / Нужны Cookies)",
            "Обновите свежие cookies через /cookies_upload или /login на сервере.",
        )

    if any(k in err_str for k in ("429", "too many requests", "rate limit")):
        return (
            "rate_limit",
            "⏳ Лимит запросов (HTTP 429 Too Many Requests)",
            "YouTube временно ограничил запросы с IP сервера. Рекомендуется подключить или сменить прокси.",
        )

    if any(k in err_str for k in ("proxy", "socks", "connection refused", "cannot connect to proxy")):
        return (
            "proxy_error",
            "🔌 Ошибка подключения к Прокси",
            "Проверьте доступность и работоспособность SOCKS5/HTTP прокси.",
        )

    if any(k in err_str for k in ("private video", "video unavailable", "not available in your country", "members-only", "georestricted", "copyright")):
        return (
            "unavailable",
            "🔒 Видео недоступно / Приватное / Геоблок",
            "Видео скрыто автором или заблокировано в регионе.",
        )

    if "timeout" in err_str or "timed out" in err_str:
        return (
            "timeout",
            "⏱ Таймаут скачивания",
            "Загрузка или обработка файла заняла слишком много времени.",
        )

    if "больше" in err_str and "mb" in err_str:
        return (
            "size_limit",
            "📦 Превышен лимит размера файла",
            "Файл превышает установленный лимит размера.",
        )

    return (
        "unknown",
        "⚠️ Ошибка загрузки медиа",
        "Проверьте логи контейнера для деталей.",
    )


def get_user_friendly_error(exc: Exception, lang: str = "ru", is_group: bool = False) -> str:
    """
    Generate localized error message for user.
    For groups: short and clean 1-line text to prevent chat spam.
    For PMs: polite and detailed explanation.
    """
    category, _, _ = classify_error(exc)

    if is_group:
        if category == "unavailable":
            return t("error.group_unavailable", lang)
        if category == "size_limit":
            return t("error.group_size_limit", lang)
        return t("error.group_generic", lang)

    # Private chat messages
    if category == "unavailable":
        return t("error.unavailable", lang)
    if category == "size_limit":
        return t("error.size_limit", lang)
    if category == "timeout":
        return t("error.timeout", lang)
    if category in {"bot_detection", "rate_limit"}:
        return t("error.service_restricted", lang)
    return t("error.generic", lang)


async def send_admin_alert(
    bot: Bot,
    redis: Redis | None,
    exc: Exception,
    *,
    url: str,
    user_id: int | None = None,
    chat_id: int | None = None,
    username: str | None = None,
) -> None:
    category, title, recommendation = classify_error(exc, url)
    error_msg = str(exc)

    # 1. Always silently log every error to SQLite database for audit & stats
    await log_error_to_db(
        user_id=user_id,
        chat_id=chat_id,
        url=url,
        error_type=category,
        error_message=error_msg,
    )

    # 2. DO NOT alert admins for routine user link errors (private video, big file, timeout, unknown URL)
    if not settings.admin_ids or category not in CRITICAL_ADMIN_CATEGORIES:
        return

    # 3. Check throttling in Redis to avoid spamming admins (max 1 alert per category every 5 minutes)
    if redis:
        cooldown_key = f"{ALERT_COOLDOWN_PREFIX}{category}"
        is_cooldown = await redis.get(cooldown_key)
        if is_cooldown:
            log.debug("Suppressing admin alert for category %s (cooldown active)", category)
            return
        await redis.set(cooldown_key, "1", ex=ALERT_COOLDOWN_SECONDS)

    # 4. Format and dispatch alert to admins
    user_str = f"ID: <code>{user_id}</code>"
    if username:
        user_str += f" (@{html.escape(username)})"

    safe_url = html.escape(url[:150])
    safe_err = html.escape(error_msg[:300])

    text = (
        f"🚨 <b>[Alert] {title}</b>\n\n"
        f"🔗 <b>Ссылка:</b> {safe_url}\n"
        f"👤 <b>Пользователь:</b> {user_str}\n"
        f"❌ <b>Ошибка:</b> <code>{safe_err}</code>\n\n"
        f"💡 <b>Рекомендация:</b> {recommendation}"
    )

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as alert_err:
            log.warning("Failed to send alert to admin %s: %s", admin_id, alert_err)
