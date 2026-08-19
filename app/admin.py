from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from .config import settings
from .database import (
    add_required_channel as db_add_required_channel,
    delete_required_channel as db_delete_required_channel,
    get_all_user_ids as db_get_all_user_ids,
    get_users_stats as db_get_users_stats,
)
from .i18n import detect_language, t
from .redis_keys import (
    ACTIVE_JOBS,
    REQUIRED_CHANNELS,
    USERS_ALL,
    USER_LANG_PREFIX,
    YTDLP_UPDATED_VERSION,
)

log = logging.getLogger(__name__)

router = Router()
redis: Redis | None = None


def set_redis(r: Redis) -> None:
    global redis
    redis = r


class AddChannelStates(StatesGroup):
    waiting_channel_id = State()
    waiting_title = State()
    waiting_invite_link = State()


class BroadcastStates(StatesGroup):
    waiting_message = State()
    confirming = State()


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.admin_ids)


async def get_admin_lang(user_id: int, user_language_code: str | None = None) -> str:
    if redis is None:
        return detect_language(user_language_code)
    saved = await redis.get(f"{USER_LANG_PREFIX}{user_id}")
    if saved:
        return saved.decode("utf-8") if isinstance(saved, bytes) else saved
    return detect_language(user_language_code)


def get_admin_main_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("admin.btn_stats", lang), callback_data="admin_stats"),
                InlineKeyboardButton(text=t("admin.btn_broadcast", lang), callback_data="admin_broadcast"),
            ],
            [
                InlineKeyboardButton(text="📢 " + t("admin.channels_title", lang).split(":")[0].strip("📢 "), callback_data="admin_channels"),
            ],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)
    await message.answer(
        t("admin.title", lang),
        reply_markup=get_admin_main_keyboard(lang),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)
    await message.answer(t("admin.broadcast_cancelled", lang), reply_markup=get_admin_main_keyboard(lang))


@router.callback_query(F.data == "admin_menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    await state.clear()
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    if callback.message:
        await callback.message.edit_text(
            t("admin.title", lang),
            reply_markup=get_admin_main_keyboard(lang),
            parse_mode="HTML",
        )
    await callback.answer()


# === Статистика ===
@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id) or redis is None:
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)

    db_stats = await db_get_users_stats()
    redis_users = await redis.scard(USERS_ALL)
    total_users = max(db_stats.get("total_users", 0), redis_users)
    channels_count = await redis.hlen(REQUIRED_CHANNELS)
    queue_len = await redis.llen(settings.queue_name)
    ytdlp_ver = await redis.get(YTDLP_UPDATED_VERSION)
    ytdlp_version_str = ytdlp_ver.decode("utf-8") if isinstance(ytdlp_ver, bytes) else (ytdlp_ver or "default")

    text = t(
        "admin.stats",
        lang,
        total_users=total_users,
        channels_count=channels_count,
        queue_len=queue_len,
        ytdlp_version=ytdlp_version_str,
    )
    if db_stats.get("total_downloads") is not None:
        text += (
            f"\n\n💾 <b>Всего скачиваний (БД):</b> {db_stats['total_downloads']}\n"
            f"👥 <b>Активных за 24ч:</b> {db_stats['active_24h']}"
        )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("admin.btn_back", lang), callback_data="admin_menu")]
        ]
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# === Обязательные каналы ===
@router.callback_query(F.data == "admin_channels")
async def cb_admin_channels(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id) or redis is None:
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)

    raw_channels = await redis.hgetall(REQUIRED_CHANNELS)
    buttons = []

    text_lines = [t("admin.channels_title", lang), ""]
    if not raw_channels:
        text_lines.append(f"<i>{t('admin.no_channels', lang)}</i>")
    else:
        for idx, (chan_id_str, raw_data) in enumerate(raw_channels.items(), 1):
            try:
                info = json.loads(raw_data)
                title = info.get("title", f"Channel {chan_id_str}")
                link = info.get("invite_link", "")
                text_lines.append(f"{idx}. <b>{title}</b> (<code>{chan_id_str}</code>)\n   🔗 {link}")
                buttons.append([
                    InlineKeyboardButton(
                        text=f"🗑 {t('admin.btn_del_channel', lang)}: {title[:20]}",
                        callback_data=f"del_chan_{chan_id_str}",
                    )
                ])
            except Exception:
                continue

    buttons.append([InlineKeyboardButton(text=t("admin.btn_add_channel", lang), callback_data="admin_add_channel")])
    buttons.append([InlineKeyboardButton(text=t("admin.btn_back", lang), callback_data="admin_menu")])

    if callback.message:
        await callback.message.edit_text(
            "\n".join(text_lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("del_chan_"))
async def cb_del_channel(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id) or redis is None:
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    chan_id = callback.data.removeprefix("del_chan_")
    await redis.hdel(REQUIRED_CHANNELS, chan_id)
    try:
        await db_delete_required_channel(int(chan_id))
    except Exception:
        pass
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    await callback.answer(t("admin.channel_deleted", lang), show_alert=True)
    await cb_admin_channels(callback)


@router.callback_query(F.data == "admin_add_channel")
async def cb_add_channel_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    await state.set_state(AddChannelStates.waiting_channel_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("admin.btn_cancel", lang), callback_data="admin_channels")]]
    )
    if callback.message:
        await callback.message.edit_text(t("admin.prompt_channel_id", lang), reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(AddChannelStates.waiting_channel_id)
async def process_channel_id(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)

    channel_id = None
    title = None
    username = None

    if message.forward_from_chat:
        channel_id = message.forward_from_chat.id
        title = message.forward_from_chat.title
        username = message.forward_from_chat.username
    elif message.text:
        text = message.text.strip()
        if text.startswith("-100") and text[4:].isdigit():
            channel_id = int(text)
        elif text.startswith("@") or not text.startswith("-"):
            username = text.lstrip("@")
            try:
                chat = await bot.get_chat(f"@{username}" if not username.startswith("@") else username)
                channel_id = chat.id
                title = chat.title
            except Exception as exc:
                log.warning("cannot find chat %s: %s", username, exc)

    if not channel_id:
        try:
            chat = await bot.get_chat(message.text.strip())
            channel_id = chat.id
            title = chat.title
        except Exception:
            pass

    if not channel_id:
        await message.answer("❌ Не удалось определить ID канала. Пожалуйста, перешлите пост из канала или укажите @username.")
        return

    await state.update_data(
        channel_id=channel_id,
        title=title or f"Channel {channel_id}",
        username=username,
    )
    await state.set_state(AddChannelStates.waiting_title)
    await message.answer(
        t("admin.prompt_channel_title", lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t("admin.btn_cancel", lang), callback_data="admin_channels")]]
        ),
        parse_mode="HTML",
    )


@router.message(AddChannelStates.waiting_title)
async def process_channel_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)
    title = message.text.strip() if message.text else "Channel"
    await state.update_data(title=title)
    await state.set_state(AddChannelStates.waiting_invite_link)
    await message.answer(
        t("admin.prompt_channel_link", lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=t("admin.btn_cancel", lang), callback_data="admin_channels")]]
        ),
        parse_mode="HTML",
    )


@router.message(AddChannelStates.waiting_invite_link)
async def process_channel_link(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None) or redis is None:
        return
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)
    invite_link = message.text.strip() if message.text else "https://t.me"

    data = await state.get_data()
    channel_id = data["channel_id"]
    title = data["title"]
    username = data.get("username")

    chan_info = {
        "channel_id": channel_id,
        "title": title,
        "invite_link": invite_link,
    }
    await redis.hset(REQUIRED_CHANNELS, str(channel_id), json.dumps(chan_info))
    try:
        await db_add_required_channel(channel_id, title, username, invite_link)
    except Exception as exc:
        log.warning("Failed to save channel to SQLite: %s", exc)
    await state.clear()

    await message.answer(
        t("admin.channel_added", lang, title=title, channel_id=channel_id),
        reply_markup=get_admin_main_keyboard(lang),
        parse_mode="HTML",
    )


# === Рассылка сообщений (Broadcast) ===
@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(t("admin.no_access", "ru"), show_alert=True)
        return
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    await state.set_state(BroadcastStates.waiting_message)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("admin.btn_cancel", lang), callback_data="admin_menu")]]
    )
    if callback.message:
        await callback.message.edit_text(t("admin.broadcast_prompt", lang), reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(BroadcastStates.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None) or redis is None:
        return
    lang = await get_admin_lang(message.from_user.id, message.from_user.language_code if message.from_user else None)
    total_users = await redis.scard(USERS_ALL)

    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await state.set_state(BroadcastStates.confirming)

    # Show preview
    await message.answer("👀 <b>Предпросмотр сообщения для рассылки выше ☝️</b>", parse_mode="HTML")
    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 " + t("admin.btn_broadcast", lang), callback_data="confirm_broadcast"),
                InlineKeyboardButton(text=t("admin.btn_cancel", lang), callback_data="cancel_broadcast"),
            ]
        ]
    )
    await message.answer(
        t("admin.broadcast_confirm", lang, count=total_users),
        reply_markup=confirm_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_broadcast")
async def cb_cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    if callback.message:
        await callback.message.edit_text(
            t("admin.broadcast_cancelled", lang),
            reply_markup=get_admin_main_keyboard(lang),
        )
    await callback.answer()


@router.callback_query(F.data == "confirm_broadcast")
async def cb_confirm_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not is_admin(callback.from_user.id) or redis is None:
        return
    lang = await get_admin_lang(callback.from_user.id, callback.from_user.language_code)
    data = await state.get_data()
    src_chat_id = data.get("broadcast_chat_id")
    src_msg_id = data.get("broadcast_message_id")
    await state.clear()

    if not src_chat_id or not src_msg_id:
        await callback.answer("Ошибка: сообщение не найдено", show_alert=True)
        return

    if callback.message:
        await callback.message.edit_text(t("admin.broadcast_started", lang), reply_markup=get_admin_main_keyboard(lang))
    await callback.answer()

    # Launch background broadcast task
    asyncio.create_task(
        run_broadcast(
            bot=bot,
            admin_chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
            src_chat_id=src_chat_id,
            src_msg_id=src_msg_id,
            admin_lang=lang,
        )
    )


async def run_broadcast(
    bot: Bot,
    admin_chat_id: int,
    src_chat_id: int,
    src_msg_id: int,
    admin_lang: str,
) -> None:
    assert redis is not None
    user_ids_set = {int(x) for x in await redis.smembers(USERS_ALL) if str(x).isdigit()}
    sqlite_uids = await db_get_all_user_ids()
    user_ids_set.update(sqlite_uids)
    total = len(user_ids_set)
    success = 0
    blocked = 0
    errors = 0
    start_time = time.monotonic()

    log.info("starting broadcast to %d users", total)
    for uid in user_ids_set:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=src_chat_id,
                message_id=src_msg_id,
            )
            success += 1
        except TelegramForbiddenError:
            blocked += 1
            # User blocked bot - remove from active set
            await redis.srem(USERS_ALL, str(uid))
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=int(raw_uid),
                    from_chat_id=src_chat_id,
                    message_id=src_msg_id,
                )
                success += 1
            except Exception:
                errors += 1
        except Exception as exc:
            log.warning("failed to send broadcast to user %s: %s", raw_uid, exc)
            errors += 1

        # Throttle ~25 msgs/sec
        await asyncio.sleep(0.04)

    duration = time.monotonic() - start_time
    report = t(
        "admin.broadcast_done",
        admin_lang,
        success=success,
        blocked=blocked,
        errors=errors,
        duration=duration,
    )
    try:
        await bot.send_message(chat_id=admin_chat_id, text=report, parse_mode="HTML")
    except Exception as exc:
        log.error("failed to send broadcast report: %s", exc)
