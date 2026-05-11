from __future__ import annotations

import asyncio
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
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Message
from redis.asyncio import Redis

from .config import settings
from .models import MediaJob
from .redis_keys import CHAT_COOLDOWN_PREFIX, CHAT_LOCK_PREFIX, JOB_PREFIX, PAUSE_FLAG

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


def _extract_supported_url(text: str | None) -> str | None:
    if not text:
        return None
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;!?)\"]}")
        host = urlparse(url).netloc.lower().split(":", 1)[0]
        if SUPPORTED_HOST_RE.search(host):
            if _is_instagram_profile_url(url):
                return None
            return url
    return None


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
        BotCommand(command="start", description="Как пользоваться ботом"),
    ]
    admin_commands = [
        BotCommand(command="start", description="Как пользоваться ботом"),
        BotCommand(command="login", description="Открыть серверный браузер для логина"),
        BotCommand(command="stories", description="Скачать Instagram stories username"),
        BotCommand(command="cookies", description="Проверить статус cookies"),
        BotCommand(command="cookies_upload", description="Загрузить cookies.txt файлом"),
        BotCommand(command="cookies_upload_instagram", description="Загрузить Instagram cookies"),
        BotCommand(command="cookies_upload_tiktok", description="Загрузить TikTok cookies"),
        BotCommand(command="cookies_upload_vk", description="Загрузить VK cookies"),
        BotCommand(command="cookies_export", description="Экспортировать Instagram cookies из браузера"),
    ]
    await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))


def _looks_like_netscape_cookies(data: bytes) -> bool:
    if not data or b"\x00" in data:
        return False
    text = data[:8192].decode("utf-8", errors="ignore")
    if "# Netscape HTTP Cookie File" in text or "# HTTP Cookie File" in text:
        return True
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if len(line.split("\t")) >= 7:
            return True
    return False


def _platform_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    name = Path(filename).name.lower()
    stem = Path(name).stem
    if stem in COOKIE_PLATFORMS:
        return stem
    for platform in COOKIE_PLATFORMS - {"cookies"}:
        if platform in name:
            return platform
    return None


def _platform_from_cookie_data(data: bytes) -> str | None:
    text = data.decode("utf-8", errors="ignore").lower()
    domains: list[str] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            domains.append(parts[0].lstrip("."))
    haystack = "\n".join(domains) if domains else text
    matches: set[str] = set()
    domain_markers = {
        "instagram": ("instagram.com", ".instagram.com"),
        "tiktok": ("tiktok.com", ".tiktok.com"),
        "vk": ("vk.com", ".vk.com", "vkvideo.ru", ".vkvideo.ru"),
        "youtube": ("youtube.com", ".youtube.com", "youtu.be", ".youtu.be", "googlevideo.com", ".googlevideo.com"),
        "twitter": ("twitter.com", ".twitter.com", "x.com", ".x.com"),
    }
    for platform, markers in domain_markers.items():
        if any(marker in haystack for marker in markers):
            matches.add(platform)
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        return "cookies"
    return None


async def _set_cookie_upload_target(message: Message, platform: str) -> None:
    assert redis is not None
    if not message.from_user:
        return
    await redis.set(f"{COOKIE_UPLOAD_PREFIX}{message.from_user.id}", platform, ex=600)
    await message.answer(
        f"Пришлите cookies-файл для <b>{platform}</b> документом в этот чат.\n\n"
        "Нужен формат Netscape cookies.txt. На Android удобнее экспортировать через Kiwi/Lemur Browser "
        "с расширением Cookie-Editor или Get cookies.txt LOCALLY."
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




async def _queue_url(message: Message, url: str, *, force_download: bool = False) -> None:
    assert redis is not None
    if await redis.get(PAUSE_FLAG):
        await _temporary_reply(message, "Обновляю загрузчик, новые задачи временно на паузе. Попробуйте чуть позже.")
        return

    url = _canonical_url(url)
    chat_id = message.chat.id
    cooldown_key = f"{CHAT_COOLDOWN_PREFIX}{chat_id}"
    if not await redis.set(cooldown_key, "1", nx=True, ex=settings.chat_cooldown_seconds):
        await _temporary_reply(message, f"Слишком часто 🙂 Подождите {settings.chat_cooldown_seconds} сек.")
        return

    lock_key = f"{CHAT_LOCK_PREFIX}{chat_id}"
    job = MediaJob.create(
        chat_id=chat_id,
        user_id=message.from_user.id if message.from_user else None,
        message_id=message.message_id,
        url=url,
        status_message_id=None,
        force_download=force_download,
    )
    if not await redis.set(lock_key, job.id, nx=True, ex=settings.active_job_idle_timeout_seconds):
        await _temporary_reply(message, "В этом чате уже есть активная загрузка. Дождитесь завершения.")
        return

    await redis.set(f"{JOB_PREFIX}{job.id}", job.dumps(), ex=settings.job_ttl_seconds)
    await redis.rpush(settings.queue_name, job.dumps())
    log.info("queued job %s chat=%s url=%s", job.id, chat_id, url)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Пришлите ссылку YouTube, TikTok, VK или Instagram. Скачаю короткое видео/фото до "
        f"{settings.max_file_mb} MB. Плейлисты и live не принимаю."
    )


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
    lines = ["Cookies status:"]
    for platform in ("instagram", "tiktok", "vk", "youtube", "twitter", "cookies"):
        path = cookies_dir / f"{platform}.txt"
        if path.exists() and path.stat().st_size > 0:
            lines.append(f"{platform}: ok, {path.stat().st_size} bytes")
        else:
            lines.append(f"{platform}: missing")
    await message.answer("\n".join(lines))


@router.message(Command("cookies_upload"))
async def cookies_upload(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer(
        "Пришлите cookies-файл документом.\n\n"
        "Лучше назвать файл по платформе: instagram.txt, tiktok.txt, vk.txt, youtube.txt или cookies.txt.\n"
        "Если имя другое, сначала используйте /cookies_upload_instagram, /cookies_upload_tiktok или /cookies_upload_vk."
    )


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
    url = _extract_supported_url(message.text or message.caption)
    if not url:
        return
    await _queue_url(message, url)


async def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    global redis
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token, session=_bot_session(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await _setup_bot_commands(bot)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
