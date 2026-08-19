from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import aiosqlite

from .config import settings

log = logging.getLogger(__name__)

_DB_PATH: Path | None = None


def get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "bot.db"
    return _DB_PATH


async def init_db(db_path: Path | None = None) -> None:
    path = db_path or get_db_path()
    log.info("Initializing SQLite database at %s", path)
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                lang TEXT DEFAULT 'ru',
                downloads_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS media_cache (
                url TEXT PRIMARY KEY,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                title TEXT,
                short_caption TEXT,
                full_caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS required_channels (
                channel_id INTEGER PRIMARY KEY,
                title TEXT,
                username TEXT,
                invite_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                url TEXT,
                error_type TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


# ---------------------------------------------------------
# User Operations
# ---------------------------------------------------------
async def upsert_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    lang: str | None = None,
) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            if lang:
                await db.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, lang, last_active_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        first_name = excluded.first_name,
                        lang = excluded.lang,
                        last_active_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, username, first_name, lang),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, last_active_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        first_name = excluded.first_name,
                        last_active_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, username, first_name),
                )
            await db.commit()
    except Exception as exc:
        log.error("Failed to upsert user %s: %s", user_id, exc)


async def get_user_lang(user_id: int) -> str | None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    except Exception as exc:
        log.error("Failed to get user lang for %s: %s", user_id, exc)
        return None


async def set_user_lang(user_id: int, lang: str) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, lang, last_active_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    lang = excluded.lang,
                    last_active_at = CURRENT_TIMESTAMP
                """,
                (user_id, lang),
            )
            await db.commit()
    except Exception as exc:
        log.error("Failed to set user lang for %s: %s", user_id, exc)


async def increment_user_downloads(user_id: int) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """
                UPDATE users SET downloads_count = downloads_count + 1, last_active_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (user_id,),
            )
            await db.commit()
    except Exception as exc:
        log.error("Failed to increment user downloads for %s: %s", user_id, exc)


async def get_all_user_ids() -> list[int]:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT user_id FROM users") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    except Exception as exc:
        log.error("Failed to get all user ids: %s", exc)
        return []


async def get_users_stats() -> dict[str, Any]:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT COUNT(*), SUM(downloads_count) FROM users") as cursor:
                total_users, total_downloads = (await cursor.fetchone()) or (0, 0)
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE last_active_at >= datetime('now', '-1 day')"
            ) as cursor:
                active_24h = ((await cursor.fetchone()) or (0,))[0]
            return {
                "total_users": total_users or 0,
                "total_downloads": total_downloads or 0,
                "active_24h": active_24h or 0,
            }
    except Exception as exc:
        log.error("Failed to get users stats: %s", exc)
        return {"total_users": 0, "total_downloads": 0, "active_24h": 0}


# ---------------------------------------------------------
# Media Cache Operations
# ---------------------------------------------------------
async def get_cached_media(url: str) -> dict[str, Any] | None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute(
                "SELECT media_type, file_id, title, short_caption, full_caption FROM media_cache WHERE url = ?",
                (url,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "media_type": row[0],
                        "file_id": row[1],
                        "title": row[2],
                        "short_caption": row[3],
                        "full_caption": row[4],
                    }
    except Exception as exc:
        log.error("Failed to get cached media for %s: %s", url, exc)
    return None


async def save_cached_media(
    url: str,
    media_type: str,
    file_id: str,
    title: str | None = None,
    short_caption: str | None = None,
    full_caption: str | None = None,
) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """
                INSERT INTO media_cache (url, media_type, file_id, title, short_caption, full_caption, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(url) DO UPDATE SET
                    media_type = excluded.media_type,
                    file_id = excluded.file_id,
                    title = excluded.title,
                    short_caption = excluded.short_caption,
                    full_caption = excluded.full_caption,
                    created_at = CURRENT_TIMESTAMP
                """,
                (url, media_type, file_id, title, short_caption, full_caption),
            )
            await db.commit()
    except Exception as exc:
        log.error("Failed to save cached media for %s: %s", url, exc)


async def delete_cached_media(url: str) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("DELETE FROM media_cache WHERE url = ?", (url,))
            await db.commit()
    except Exception as exc:
        log.error("Failed to delete cached media for %s: %s", url, exc)


# ---------------------------------------------------------
# Required Channels Operations
# ---------------------------------------------------------
async def get_required_channels() -> list[dict[str, Any]]:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute(
                "SELECT channel_id, title, username, invite_link FROM required_channels"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "channel_id": r[0],
                        "title": r[1],
                        "username": r[2],
                        "invite_link": r[3],
                    }
                    for r in rows
                ]
    except Exception as exc:
        log.error("Failed to get required channels: %s", exc)
        return []


async def add_required_channel(
    channel_id: int,
    title: str | None = None,
    username: str | None = None,
    invite_link: str | None = None,
) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """
                INSERT INTO required_channels (channel_id, title, username, invite_link)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username,
                    invite_link = excluded.invite_link
                """,
                (channel_id, title, username, invite_link),
            )
            await db.commit()
    except Exception as exc:
        log.error("Failed to add required channel %s: %s", channel_id, exc)


async def delete_required_channel(channel_id: int) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("DELETE FROM required_channels WHERE channel_id = ?", (channel_id,))
            await db.commit()
    except Exception as exc:
        log.error("Failed to delete required channel %s: %s", channel_id, exc)


# ---------------------------------------------------------
# Error Logging Operations
# ---------------------------------------------------------
async def log_error_to_db(
    user_id: int | None,
    chat_id: int | None,
    url: str | None,
    error_type: str,
    error_message: str,
) -> None:
    try:
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """
                INSERT INTO error_logs (user_id, chat_id, url, error_type, error_message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, chat_id, url, error_type, error_message[:1000]),
            )
            await db.commit()
    except Exception as exc:
        log.error("Failed to log error to db: %s", exc)
