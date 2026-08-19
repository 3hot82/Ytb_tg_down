from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from io import BytesIO
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from . import admin
from .config import settings
from .database import (
    delete_cached_media as db_delete_cached_media,
    get_cached_media as db_get_cached_media,
    get_required_channels as db_get_required_channels,
    get_user_lang as db_get_user_lang,
    init_db,
    set_user_lang as db_set_user_lang,
    upsert_user,
)
from .i18n import detect_language, t
from .middlewares import RateLimitMiddleware, SubscriptionMiddleware, rate_limit_cleanup_loop
from .models import MediaJob
from .redis_keys import (
    JOB_PREFIX,
    MEDIA_CACHE_PREFIX,
    PAUSE_FLAG,
    PENDING_JOBS_CHAT_PREFIX,
    PENDING_JOBS_USER_PREFIX,
    REQUIRED_CHANNELS,
    URL_INFLIGHT_PREFIX,
    URL_WAITERS_PREFIX,
    USERS_ALL,
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



_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "igsh", "si", "feature", "pp", "share", "app", "source"}


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower().split(":", 1)[0]
    path = parsed.path.rstrip("/") or "/"
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in _TRACKING_QUERY_KEYS or any(key_lower.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))
    query = urlencode(sorted(query_items))
    return urlunparse((scheme, host, path, "", query, ""))


def _force_download_arg(text: str | None) -> str | None:
    args = (text or "").split(maxsplit=1)
    return args[1] if len(args) > 1 else None


URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
SUPPORTED_HOST_RE = re.compile(
    r"(^|\.)(youtube\.com|youtu\.be|tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|vk\.com|m\.vk\.com|vkvideo\.ru|instagram\.com|www\.instagram\.com|instagr\.am)$",
    re.IGNORECASE,
)
COOKIE_PLATFORMS = {"instagram", "tiktok", "vk", "youtube", "twitter", "cookies"}
COOKIE_UPLOAD_PREFIX = "cookie_upload:"

router = Router()
redis: Redis | None = None


async def _track_user(user: Any) -> None:
    if not user or redis is None:
        return
    try:
        await redis.sadd(USERS_ALL, str(user.id))
        lang = None
        if not await redis.exists(f"{USER_LANG_PREFIX}{user.id}"):
            sqlite_lang = await db_get_user_lang(user.id)
            if sqlite_lang:
                lang = sqlite_lang
                await redis.set(f"{USER_LANG_PREFIX}{user.id}", lang)
            else:
                lang = detect_language(getattr(user, "language_code", None))
                await redis.set(f"{USER_LANG_PREFIX}{user.id}", lang)
        await upsert_user(
            user_id=user.id,
            username=getattr(user, "username", None),
            first_name=getattr(user, "first_name", None),
            lang=lang,
        )
    except Exception as exc:
        log.debug("failed to track user %s: %s", getattr(user, "id", None), exc)


async def _get_user_lang(user_id: int | None, language_code: str | None = None) -> str:
    if not user_id or redis is None:
        return detect_language(language_code)
    try:
        saved = await redis.get(f"{USER_LANG_PREFIX}{user_id}")
        if saved:
            return saved.decode("utf-8") if isinstance(saved, bytes) else saved
    except Exception:
        pass
    return detect_language(language_code)




def _instagram_profile_username(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return None
    username = parts[0].lstrip("@")
    reserved = {"p", "reel", "reels", "stories", "explore", "accounts", "direct", "tv"}
    if username.lower() in reserved or not INSTAGRAM_USERNAME_RE.fullmatch(username):
        return None
    return username


def _instagram_stories_url(username: str) -> str:
    return urlunparse(("https", "www.instagram.com", f"/stories/{username}/", "", "", ""))


def _stories_url_from_arg(arg: str) -> str | None:
    arg = arg.strip()
    profile_username = _instagram_profile_username(arg)
    if profile_username:
        return _instagram_stories_url(profile_username)
    username = arg.lstrip("@")
    if INSTAGRAM_USERNAME_RE.fullmatch(username):
        return _instagram_stories_url(username)
    return None


def _is_instagram_profile_url(url: str) -> bool:
    return _instagram_profile_username(url) is not None


def _extract_supported_urls(text: str | None) -> list[str]:
    if not text:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).rstrip(".,;!?)\"]}")
        host = urlparse(raw_url).netloc.lower().split(":", 1)[0]
        if not SUPPORTED_HOST_RE.search(host):
            continue
        if _is_instagram_profile_url(raw_url):
            continue
        url = _canonical_url(raw_url)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls

def _extract_supported_url(text: str | None) -> str | None:
    urls = _extract_supported_urls(text)
    return urls[0] if urls else None

async def _try_send_cached(message: Message, url: str) -> bool:
    assert redis is not None
    raw = await redis.get(f"{MEDIA_CACHE_PREFIX}{url}")
    media_type, file_id, caption = None, None, None

    if raw:
        media_type, file_id, caption = (raw.split("\t", 2) + [None, None, None])[:3]
    else:
        # Fallback to SQLite permanent cache
        cached = await db_get_cached_media(url)
        if cached:
            media_type = cached.get("media_type")
            file_id = cached.get("file_id")
            caption = cached.get("full_caption") or cached.get("short_caption") or cached.get("title")
            # Populate back to Redis
            if media_type and file_id:
                await redis.set(f"{MEDIA_CACHE_PREFIX}{url}", f"{media_type}\t{file_id}\t{caption or ''}", ex=settings.media_cache_ttl_seconds)

    if media_type not in {"photo", "video", "document"} or not file_id:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{url}")
        return False
    try:
        if media_type == "photo":
            await message.answer_photo(file_id, caption=caption or None, reply_to_message_id=message.message_id)
        elif media_type == "video":
            await message.answer_video(file_id, caption=caption or None, reply_to_message_id=message.message_id, supports_streaming=True)
        else:
            await message.answer_document(file_id, caption=caption or None, reply_to_message_id=message.message_id)
        log.info("sent from telegram file_id cache chat=%s url=%s type=%s", message.chat.id, url, media_type)
        return True
    except TelegramBadRequest as exc:
        await redis.delete(f"{MEDIA_CACHE_PREFIX}{url}")
        await db_delete_cached_media(url)
        log.warning("telegram file_id cache failed for %s, invalidating and queueing: %s", url, exc)
        return False

def _pending_chat_key(chat_id: int) -> str:
    return f"{PENDING_JOBS_CHAT_PREFIX}{chat_id}"

def _pending_user_key(user_id: int) -> str:
    return f"{PENDING_JOBS_USER_PREFIX}{user_id}"

async def _reserve_pending(jobs: list[MediaJob]) -> bool:
    assert redis is not None
    if not jobs:
        return True
    chat_id = jobs[0].chat_id
    user_id = jobs[0].user_id
    chat_key = _pending_chat_key(chat_id)
    user_key = _pending_user_key(user_id) if user_id is not None else None
    chat_pending = await redis.scard(chat_key)
    if chat_pending + len(jobs) > settings.max_pending_jobs_per_chat:
        return False
    if user_key:
        user_pending = await redis.scard(user_key)
        if user_pending + len(jobs) > settings.max_pending_jobs_per_user:
            return False
    job_ids = [job.id for job in jobs]
    ttl = settings.pending_job_ttl_seconds
    pipe = redis.pipeline()
    pipe.sadd(chat_key, *job_ids)
    pipe.expire(chat_key, ttl)
    if user_key:
        pipe.sadd(user_key, *job_ids)
        pipe.expire(user_key, ttl)
    await pipe.execute()
    return True

async def _release_pending(jobs: list[MediaJob]) -> None:
    assert redis is not None
    if not jobs:
        return
    pipe = redis.pipeline()
    for job in jobs:
        pipe.srem(_pending_chat_key(job.chat_id), job.id)
        if job.user_id is not None:
            pipe.srem(_pending_user_key(job.user_id), job.id)
    await pipe.execute()

async def _queue_urls(message: Message, urls: list[str], *, force_download: bool = False) -> None:
    assert redis is not None
    # Drop stale messages (older than 2 minutes) to prevent backlog processing upon restart
    if message.date:
        msg_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - msg_date).total_seconds()
        if age > 120:
            log.warning("ignoring stale message age=%.1fs chat=%s msg_id=%s", age, message.chat.id, message.message_id)
            return

    await _track_user(message.from_user)
    user_id = message.from_user.id if message.from_user else None
    user_lang = await _get_user_lang(user_id, message.from_user.language_code if message.from_user else None)

    if await redis.get(PAUSE_FLAG):
        await _temporary_reply(message, t("queue.pause", user_lang))
        return

    urls = list(dict.fromkeys(_canonical_url(url) for url in urls))
    if not urls:
        return
    if len(urls) > settings.max_urls_per_message:
        await _temporary_reply(message, t("queue.too_many_urls", user_lang, max_urls=settings.max_urls_per_message))
        return

    queue_urls: list[str] = []
    if force_download:
        queue_urls = urls
    else:
        for url in urls:
            if not await _try_send_cached(message, url):
                queue_urls.append(url)
    if not queue_urls:
        return

    # Dedup URLs already in-flight (queued or being downloaded).
    # force_download bypasses the check and sets fresh inflight lock.
    # If URL is already inflight, register as waiter — worker will auto-send.
    filtered_urls: list[str] = []
    inflight_ttl = max(settings.job_ttl_seconds, settings.pending_job_ttl_seconds)
    for url in queue_urls:
        if force_download:
            await redis.delete(f"{URL_INFLIGHT_PREFIX}{url}")
        else:
            locked = await redis.set(f"{URL_INFLIGHT_PREFIX}{url}", "1", nx=True, ex=inflight_ttl)
            if not locked:
                # URL already queued/downloading — register waiter for auto-send
                waiter_key = f"{URL_WAITERS_PREFIX}{url}"
                waiter = json.dumps({"chat_id": message.chat.id, "message_id": message.message_id})
                await redis.sadd(waiter_key, waiter)
                await redis.expire(waiter_key, inflight_ttl)
                # Check cache one more time in case download finished between queue and now
                if await _try_send_cached(message, url):
                    await redis.srem(waiter_key, waiter)
                    log.info("url now cached, waiter satisfied chat=%s url=%s", message.chat.id, url)
                else:
                    log.info("url already in-flight, registered waiter chat=%s url=%s", message.chat.id, url)
                continue
        filtered_urls.append(url)
    if not filtered_urls:
        return
    queue_urls = filtered_urls

    chat_id = message.chat.id
    is_direct_chat = message.chat.type == "private"
    progress_message_id = None
    if is_direct_chat:
        status = await message.answer(t("queue.wait", user_lang), reply_to_message_id=message.message_id)
        progress_message_id = status.message_id
    jobs = [
        MediaJob.create(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message.message_id,
            url=url,
            status_message_id=None,
            progress_chat_id=chat_id if is_direct_chat else None,
            progress_message_id=progress_message_id,
            force_download=force_download,
        )
        for url in queue_urls
    ]
    if not await _reserve_pending(jobs):
        await _temporary_reply(
            message,
            t("queue.full", user_lang, max_chat=settings.max_pending_jobs_per_chat, max_user=settings.max_pending_jobs_per_user),
        )
        return

    try:
        pipe = redis.pipeline()
        for job in jobs:
            pipe.set(f"{JOB_PREFIX}{job.id}", job.dumps(), ex=settings.job_ttl_seconds)
            pipe.rpush(settings.queue_name, job.dumps())
        await pipe.execute()
    except Exception:
        await _release_pending(jobs)
        raise
    for job in jobs:
        log.info("queued job %s chat=%s user=%s url=%s", job.id, chat_id, user_id, job.url)

async def _queue_url(message: Message, url: str, *, force_download: bool = False) -> None:
    await _queue_urls(message, [url], force_download=force_download)

def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


async def _delete_later(message: Message, delay: int = 5) -> None:
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except (TelegramBadRequest, TelegramForbiddenError):
        log.debug("failed to delete service message", exc_info=True)


async def _temporary_reply(message: Message, text: str, *, delay: int = 5) -> None:
    reply = await message.reply(text)
    asyncio.create_task(_delete_later(reply, delay=delay))


async def _setup_bot_commands(bot: Bot) -> None:
    public_commands = [
        BotCommand(command="start", description="Главное меню / Main menu"),
        BotCommand(command="lang", description="Сменить язык / Switch language"),
        BotCommand(command="help", description="Справка / Help"),
        BotCommand(command="stories", description="Скачать Instagram stories username"),
        BotCommand(command="redownload", description="Заново скачать видео, обойти кэш"),
    ]
    admin_commands = [
        BotCommand(command="admin", description="🛠 Админ-панель (ОП, статистика, рассылка)"),
        BotCommand(command="start", description="Главное меню / Main menu"),
        BotCommand(command="lang", description="Сменить язык / Switch language"),
        BotCommand(command="help", description="Справка / Help"),
        BotCommand(command="redownload", description="Заново скачать видео, обойти кэш"),
        BotCommand(command="stories", description="Скачать Instagram stories username"),
        BotCommand(command="cookies", description="Проверить статус cookies"),
        BotCommand(command="cookies_help", description="📖 Инструкция как получить cookies"),
        BotCommand(command="cookies_upload", description="Загрузить cookies.txt файлом"),
        BotCommand(command="cookies_upload_youtube", description="Загрузить YouTube cookies"),
        BotCommand(command="cookies_upload_instagram", description="Загрузить Instagram cookies"),
        BotCommand(command="cookies_upload_tiktok", description="Загрузить TikTok cookies"),
        BotCommand(command="cookies_upload_vk", description="Загрузить VK cookies"),
        BotCommand(command="cookies_export", description="Экспортировать Instagram cookies из браузера"),
        BotCommand(command="login", description="Открыть серверный браузер для логина"),
    ]
    await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))


def _looks_like_netscape_cookies(data: bytes) -> bool:
    if not data or b"\x00" in data:
        return False
    text = data[:8192].decode("utf-8", errors="ignore")
    return (
        "# Netscape HTTP Cookie File" in text
        or "# HTTP Cookie File" in text
        or "\tTRUE\t" in text
        or "\tFALSE\t" in text
    )


def _platform_from_filename(filename: str) -> str | None:
    lower = filename.lower()
    for name in ("instagram", "tiktok", "vk", "youtube", "twitter"):
        if name in lower:
            return name
    if lower == "cookies.txt":
        return "cookies"
    return None


def _platform_from_cookie_data(data: bytes) -> str | None:
    text = data[:8192].decode("utf-8", errors="ignore").lower()
    if "instagram.com" in text:
        return "instagram"
    if "tiktok.com" in text:
        return "tiktok"
    if "vk.com" in text or "vkontakte.ru" in text:
        return "vk"
    if "youtube.com" in text or "google.com" in text:
        return "youtube"
    if "twitter.com" in text or "x.com" in text:
        return "twitter"
    return None


async def _set_cookie_upload_target(message: Message, platform: str) -> None:
    assert redis is not None
    if not message.from_user:
        return
    await redis.set(f"{COOKIE_UPLOAD_PREFIX}{message.from_user.id}", platform, ex=600)
    await message.answer(
        f"Пришлите cookies-файл для <b>{platform}</b> документом в этот чат.\n\n"
        "Нужен формат Netscape cookies.txt. Подробная инструкция: /cookies_help",
        parse_mode="HTML",
    )


async def _export_cookies(platform: str = "instagram") -> tuple[bool, str]:
    cookies_path = Path(settings.data_dir) / "cookies" / f"{platform}.txt"
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cookies_path.with_suffix(".tmp")
    cmd = [
        settings.ytdlp_bin,
        "--cookies-from-browser",
        f"chromium:{settings.browser_profile_path}",
        "--cookies",
        str(tmp_path),
        "--skip-download",
        "--simulate",
        "https://www.instagram.com/",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace")
    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        return False, output[-1200:]
    if not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        return False, "cookies file was not created or is empty"
    tmp_path.replace(cookies_path)
    try:
        cookies_path.chmod(0o600)
    except OSError:
        pass
    return True, f"exported {cookies_path.stat().st_size} bytes to {cookies_path}"


@router.message(CommandStart())
async def start(message: Message) -> None:
    await _track_user(message.from_user)
    lang = await _get_user_lang(message.from_user.id if message.from_user else None, message.from_user.language_code if message.from_user else None)
    name = message.from_user.first_name if message.from_user else "User"
    await message.answer(t("start.welcome", lang, name=name))


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await _track_user(message.from_user)
    lang = await _get_user_lang(message.from_user.id if message.from_user else None, message.from_user.language_code if message.from_user else None)
    await message.answer(t("help.text", lang))


@router.message(Command("lang", "language"))
async def lang_cmd(message: Message) -> None:
    await _track_user(message.from_user)
    lang = await _get_user_lang(message.from_user.id if message.from_user else None, message.from_user.language_code if message.from_user else None)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_en"),
            ]
        ]
    )
    await message.answer(t("lang.choose", lang), reply_markup=kb)


@router.callback_query(F.data.in_({"set_lang_ru", "set_lang_en"}))
async def cb_set_lang(callback: CallbackQuery) -> None:
    if not callback.from_user or redis is None:
        return
    new_lang = "ru" if callback.data == "set_lang_ru" else "en"
    await redis.set(f"{USER_LANG_PREFIX}{callback.from_user.id}", new_lang)
    await db_set_user_lang(callback.from_user.id, new_lang)
    await _track_user(callback.from_user)
    if callback.message:
        await callback.message.edit_text(t("lang.changed", new_lang))
    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or redis is None:
        return
    user_id = callback.from_user.id
    lang = await _get_user_lang(user_id, callback.from_user.language_code)
    raw_channels = await redis.hgetall(REQUIRED_CHANNELS)
    not_subscribed = False
    for chan_id_str in raw_channels:
        try:
            member = await bot.get_chat_member(chat_id=int(chan_id_str), user_id=user_id)
            if member.status not in {"creator", "administrator", "member", "restricted"}:
                not_subscribed = True
                break
        except Exception:
            not_subscribed = True
            break

    if not_subscribed:
        await callback.answer(t("sub.not_yet", lang), show_alert=True)
        return

    await callback.answer(t("sub.success", lang), show_alert=True)
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass

    # Check if there is a pending URL from before subscription
    pending_key = f"ytbot:pending_url:{user_id}"
    pending_text = await redis.get(pending_key)
    if pending_text and callback.message:
        await redis.delete(pending_key)
        urls = _extract_supported_urls(pending_text)
        if urls:
            fake_msg = callback.message
            fake_msg.from_user = callback.from_user
            await _queue_urls(fake_msg, urls)


@router.message(Command("login"))
async def login(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer(
        "Открой серверный Chromium для логина:\n"
        f"{settings.auth_browser_url}\n\n"
        "Зайди в Instagram внутри этого браузера. После успешного входа отправь /cookies_export.\n"
        "Если браузер не открывается — сначала подними сервис: docker compose --profile auth up -d auth-browser"
    )


@router.message(Command("cookies"))
async def cookies_status(message: Message) -> None:
    if not _is_admin(message):
        return
    cookies_dir = Path(settings.data_dir) / "cookies"
    lines = ["<b>Статус Cookies:</b>"]
    for platform in ("youtube", "instagram", "tiktok", "vk", "twitter", "cookies"):
        path = cookies_dir / f"{platform}.txt"
        if path.exists() and path.stat().st_size > 0:
            lines.append(f"• <b>{platform}</b>: ✅ активен ({path.stat().st_size} байт)")
        else:
            lines.append(f"• <b>{platform}</b>: ❌ отсутствует")
    lines.append("\n📖 <i>Инструкция как получить cookies:</i> /cookies_help")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("cookies_help", "help_cookies", "cookies_info"))
async def cookies_help_handler(message: Message) -> None:
    if not _is_admin(message):
        return
    text = (
        "📖 <b>Как экспортировать и загрузить Cookies в бота:</b>\n\n"
        "📱 <b>С телефона (Android / Kiwi Browser):</b>\n"
        "1. Установите <b>Kiwi Browser</b> (или Lemur Browser) из Google Play.\n"
        "2. В браузере установите расширение <b>Cookie-Editor</b> (из Chrome Web Store).\n"
        "3. Откройте нужный сайт (youtube.com / instagram.com) и войдите в свой аккаунт.\n"
        "4. Нажмите меню <b>⋮</b> в правом верхнем углу ➔ выберите <b>Cookie-Editor</b>.\n"
        "5. Нажмите <b>Export</b> ➔ выберите <b>Export as Netscape / cookies.txt</b>.\n"
        "6. Переименуйте файл в <code>youtube.txt</code> или <code>instagram.txt</code> (или отправьте команду /cookies_upload_youtube).\n"
        "7. <b>Отправьте этот файл боту как документ.</b>\n\n"
        "💻 <b>С компьютера (Chrome / Firefox / Edge):</b>\n"
        "1. Установите расширение <b>Cookie-Editor</b> или <b>Get cookies.txt LOCALLY</b>.\n"
        "2. Войдите на сайт (YouTube, Instagram) и нажмите <i>Export as cookies.txt</i>.\n"
        "3. Отправьте файл <code>youtube.txt</code> или <code>instagram.txt</code> документом в этот чат.\n\n"
        "🔍 <b>Команды управления:</b>\n"
        "• /cookies — проверить статус загруженных cookies\n"
        "• /cookies_upload_youtube — подготовить загрузку YouTube\n"
        "• /cookies_upload_instagram — подготовить загрузку Instagram\n"
        "• /cookies_upload_tiktok — подготовить загрузку TikTok\n"
        "• /cookies_upload_vk — подготовить загрузку VK"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("cookies_upload"))
async def cookies_upload(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer(
        "Пришлите cookies-файл документом.\n\n"
        "Лучше назвать файл по платформе: <code>youtube.txt</code>, <code>instagram.txt</code>, <code>tiktok.txt</code>, <code>vk.txt</code> или <code>cookies.txt</code>.\n"
        "Если имя другое, сначала используйте команду платформы (например, /cookies_upload_youtube).\n\n"
        "📖 Подробная инструкция: /cookies_help",
        parse_mode="HTML",
    )


@router.message(Command("cookies_upload_youtube"))
async def cookies_upload_youtube(message: Message) -> None:
    if not _is_admin(message):
        return
    await _set_cookie_upload_target(message, "youtube")


@router.message(Command("cookies_upload_instagram"))
async def cookies_upload_instagram(message: Message) -> None:
    if not _is_admin(message):
        return
    await _set_cookie_upload_target(message, "instagram")


@router.message(Command("cookies_upload_tiktok"))
async def cookies_upload_tiktok(message: Message) -> None:
    if not _is_admin(message):
        return
    await _set_cookie_upload_target(message, "tiktok")


@router.message(Command("cookies_upload_vk"))
async def cookies_upload_vk(message: Message) -> None:
    if not _is_admin(message):
        return
    await _set_cookie_upload_target(message, "vk")


@router.message(F.document)
async def receive_cookies_file(message: Message, bot: Bot) -> None:
    if not _is_admin(message):
        return
    assert redis is not None
    if not message.document:
        return

    filename = message.document.file_name or ""

    if message.document.file_size and message.document.file_size > 2_000_000:
        await message.answer("Файл слишком большой для cookies.txt. Проверьте, что отправляете правильный файл.")
        return

    tg_file = await bot.get_file(message.document.file_id)
    buffer = BytesIO()
    await bot.download_file(tg_file.file_path, destination=buffer)
    data = buffer.getvalue()
    if not _looks_like_netscape_cookies(data):
        await message.answer("Файл не похож на Netscape cookies.txt. Экспортируйте cookies именно в формате cookies.txt.")
        return

    filename_platform = _platform_from_filename(filename)
    detected_platform = _platform_from_cookie_data(data)
    pending_platform = await redis.get(f"{COOKIE_UPLOAD_PREFIX}{message.from_user.id}") if message.from_user else None

    platform = None
    if pending_platform:
        platform = pending_platform
    elif filename_platform and filename_platform != "cookies":
        platform = filename_platform
    elif detected_platform:
        platform = detected_platform
    else:
        platform = filename_platform

    if not platform:
        await message.answer(
            "Не понял, для какой платформы cookies.\n"
            "Назовите файл instagram.txt/tiktok.txt/vk.txt или сначала отправьте /cookies_upload_instagram."
        )
        return

    cookies_dir = Path(settings.data_dir) / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    path = cookies_dir / f"{platform}.txt"
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    if message.from_user:
        await redis.delete(f"{COOKIE_UPLOAD_PREFIX}{message.from_user.id}")
    source = "определил по содержимому" if detected_platform and platform == detected_platform else "выбрал по команде/имени файла"
    await message.answer(f"✅ Cookies для {platform} сохранены: {path.stat().st_size} bytes ({source})")


@router.message(Command("cookies_export"))
async def cookies_export(message: Message) -> None:
    if not _is_admin(message):
        return
    status = await message.answer("Экспортирую Instagram cookies…")
    ok, details = await _export_cookies("instagram")
    text = "✅ Cookies экспортированы" if ok else "❌ Не удалось экспортировать cookies"
    await status.edit_text(f"{text}\n{details}")


@router.message(Command("redownload"))
async def redownload(message: Message) -> None:
    raw = _force_download_arg(message.text or message.caption)
    url = _extract_supported_url(raw) if raw else None
    if not url:
        await message.answer("Напишите ссылку после команды, например:\n/redownload https://youtu.be/...")
        return
    await _queue_url(message, url, force_download=True)


@router.message(Command("stories"))
async def instagram_stories(message: Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Напишите username после команды, например:\n/stories instagram")
        return
    stories_url = _stories_url_from_arg(args[1])
    if not stories_url:
        await message.answer("Не понял username или ссылку на профиль. Пример:\n/stories instagram\n/stories https://www.instagram.com/instagram")
        return
    await _queue_url(message, stories_url)


@router.message(F.text | F.caption)
async def catch_media_link(message: Message) -> None:
    urls = _extract_supported_urls(message.text or message.caption)
    if not urls:
        return
    await _queue_urls(message, urls)


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    await init_db()

    global redis
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    admin.set_redis(redis)

    # Sync required channels from SQLite to Redis if Redis was restarted
    try:
        sqlite_channels = await db_get_required_channels()
        if sqlite_channels and not await redis.exists(REQUIRED_CHANNELS):
            for ch in sqlite_channels:
                await redis.hset(REQUIRED_CHANNELS, str(ch["channel_id"]), json.dumps(ch))
    except Exception as exc:
        log.warning("Failed to sync required channels from SQLite: %s", exc)

    bot = Bot(settings.bot_token, session=_bot_session(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Rate limiting middleware (in-memory, 5 req/min per user)
    dp.message.middleware(RateLimitMiddleware())

    # Middleware for forced subscription check
    sub_middleware = SubscriptionMiddleware(redis)
    dp.message.outer_middleware(sub_middleware)
    dp.callback_query.outer_middleware(sub_middleware)

    # Include routers (admin router handles /admin and broadcast)
    dp.include_router(admin.router)
    dp.include_router(router)

    await _setup_bot_commands(bot)
    cleanup_task = asyncio.create_task(rate_limit_cleanup_loop())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        cleanup_task.cancel()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
