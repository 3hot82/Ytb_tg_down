from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from redis.asyncio import Redis

from ..config import settings
from ..i18n import detect_language, t
from ..redis_keys import REQUIRED_CHANNELS, USER_LANG_PREFIX

log = logging.getLogger(__name__)

SKIP_CALLBACKS = {
    "check_sub",
    "set_lang_ru",
    "set_lang_en",
    "confirm_broadcast",
    "cancel_broadcast",
}
ALLOWED_COMMANDS = {"/start", "/help", "/lang", "/admin", "/cancel"}


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(self, redis: Redis) -> None:
        super().__init__()
        self.redis = redis

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data.get("bot") or event.bot  # type: ignore
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        # Only enforce subscription check in private chats (личка с ботом).
        # In group chats, supergroups, and channels (chat_id < 0), allow downloading without subscription check.
        chat = getattr(event, "chat", None)
        if not chat and isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        if chat and chat.type != "private":
            return await handler(event, data)

        # Allow admins without check
        if user.id in settings.admin_ids:
            return await handler(event, data)

        # Allow specific callbacks without check
        if isinstance(event, CallbackQuery):
            cb_data = event.data or ""
            if cb_data in SKIP_CALLBACKS or cb_data.startswith("admin_") or cb_data.startswith("set_lang_") or cb_data.startswith("del_chan_"):
                return await handler(event, data)

        # Allow basic info/config commands without check
        if isinstance(event, Message) and event.text:
            cmd = event.text.strip().split()[0].lower()
            if cmd in ALLOWED_COMMANDS:
                return await handler(event, data)

        # Fetch required channels from Redis
        raw_channels = await self.redis.hgetall(REQUIRED_CHANNELS)
        if not raw_channels:
            return await handler(event, data)

        # Check subscription status for all channels
        not_subscribed: list[dict[str, Any]] = []
        for chan_id_str, raw_data in raw_channels.items():
            try:
                chan_id = int(chan_id_str)
                info = json.loads(raw_data)
                member = await bot.get_chat_member(chat_id=chan_id, user_id=user.id)
                if member.status not in {"creator", "administrator", "member", "restricted"}:
                    not_subscribed.append(info)
            except Exception as exc:
                log.warning("failed checking subscription for user %s in channel %s: %s", user.id, chan_id_str, exc)
                # If bot cannot check or user not in channel, require subscription
                try:
                    info = json.loads(raw_data)
                    not_subscribed.append(info)
                except Exception:
                    pass

        if not not_subscribed:
            return await handler(event, data)

        # Get user's preferred language
        saved_lang = await self.redis.get(f"{USER_LANG_PREFIX}{user.id}")
        lang = saved_lang.decode("utf-8") if isinstance(saved_lang, bytes) else (saved_lang or detect_language(user.language_code))

        # Build inline keyboard with unsubscribed channels and check button
        keyboard_buttons = []
        for chan in not_subscribed:
            title = chan.get("title") or "📢 Подписаться / Subscribe"
            invite_link = chan.get("invite_link") or "https://t.me"
            keyboard_buttons.append([InlineKeyboardButton(text=f"📢 {title}", url=invite_link)])

        keyboard_buttons.append([
            InlineKeyboardButton(text=t("sub.check_btn", lang), callback_data="check_sub")
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        text = t("sub.required", lang)

        if isinstance(event, Message):
            # Save pending text/URL so user doesn't have to resend after subscribing
            if event.text:
                await self.redis.set(f"ytbot:pending_url:{user.id}", event.text.strip(), ex=600)
            await event.answer(text, reply_markup=markup, parse_mode="HTML")
        elif isinstance(event, CallbackQuery):
            if event.message:
                await event.message.answer(text, reply_markup=markup, parse_mode="HTML")
            await event.answer()

        return None
