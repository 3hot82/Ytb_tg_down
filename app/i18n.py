from __future__ import annotations

from typing import Any

TRANSLATIONS: dict[str, dict[str, str]] = {
    # === /start ===
    "start.welcome": {
        "ru": (
            "👋 <b>Привет, {name}!</b>\n\n"
            "📥 Я помогу тебе скачать медиа по ссылкам:\n"
            "• <b>YouTube</b> (видео, Shorts, музыка)\n"
            "• <b>TikTok</b> (видео без водяных знаков)\n"
            "• <b>Instagram</b> (посты, Reels, Stories)\n"
            "• <b>VK</b> (клипы и видео)\n\n"
            "📌 <b>Как пользоваться:</b>\n"
            "Просто отправь мне ссылку — и я скачаю её для тебя!\n\n"
            "🌐 Сменить язык: /lang\n"
            "ℹ️ Справка: /help"
        ),
        "en": (
            "👋 <b>Hello, {name}!</b>\n\n"
            "📥 I will help you download media via links:\n"
            "• <b>YouTube</b> (videos, Shorts, music)\n"
            "• <b>TikTok</b> (videos without watermark)\n"
            "• <b>Instagram</b> (posts, Reels, Stories)\n"
            "• <b>VK</b> (clips and videos)\n\n"
            "📌 <b>How to use:</b>\n"
            "Just send me a link — and I'll download it for you!\n\n"
            "🌐 Change language: /lang\n"
            "ℹ️ Help: /help"
        ),
    },

    # === /help ===
    "help.text": {
        "ru": (
            "📖 <b>Справка по боту:</b>\n\n"
            "1. Отправь любую поддерживаемую ссылку:\n"
            "   • YouTube / Shorts / Music\n"
            "   • TikTok / Reels\n"
            "   • Instagram (посты, reels, stories)\n"
            "   • VK Video\n"
            "2. Для Instagram Stories отправь: <code>/stories username</code> или ссылку на профиль/историю.\n"
            "3. Бот автоматически подготовит файл в высоком качестве и нормализует звук.\n\n"
            "⚙️ <b>Команды:</b>\n"
            "/start — Главное меню\n"
            "/lang — Смена языка (RU / EN)\n"
            "/help — Эта справка\n"
            "/stories [user] — Скачать Stories из Instagram"
        ),
        "en": (
            "📖 <b>Bot Help:</b>\n\n"
            "1. Send any supported link:\n"
            "   • YouTube / Shorts / Music\n"
            "   • TikTok / Reels\n"
            "   • Instagram (posts, reels, stories)\n"
            "   • VK Video\n"
            "2. For Instagram Stories send: <code>/stories username</code> or profile/story link.\n"
            "3. The bot will automatically prepare the media in high quality and normalize volume.\n\n"
            "⚙️ <b>Commands:</b>\n"
            "/start — Main menu\n"
            "/lang — Switch language (RU / EN)\n"
            "/help — This help message\n"
            "/stories [user] — Download Instagram Stories"
        ),
    },

    # === Language switcher ===
    "lang.choose": {
        "ru": "🌐 <b>Выберите язык интерфейса / Choose your language:</b>",
        "en": "🌐 <b>Choose your interface language / Выберите язык:</b>",
    },
    "lang.changed": {
        "ru": "✅ Язык успешно изменен на <b>Русский</b> 🇷🇺",
        "en": "✅ Language successfully changed to <b>English</b> 🇬🇧",
    },

    # === Queue & status messages ===
    "queue.pause": {
        "ru": "⏳ Обновляю загрузчик, новые задачи временно на паузе. Попробуйте чуть позже.",
        "en": "⏳ Updating downloader runtime, new jobs are temporarily paused. Please try again shortly.",
    },
    "queue.too_many_urls": {
        "ru": "⚠️ Слишком много ссылок в одном сообщении: максимум {max_urls}.",
        "en": "⚠️ Too many links in one message: maximum {max_urls}.",
    },
    "queue.full": {
        "ru": (
            "⚠️ Очередь заполнена. Лимиты: "
            "{max_chat} задач на чат и {max_user} задач на пользователя."
        ),
        "en": (
            "⚠️ Queue is full. Limits: "
            "{max_chat} jobs per chat and {max_user} jobs per user."
        ),
    },
    "queue.wait": {
        "ru": "⏳ Принял ссылку. Жду очередь…",
        "en": "⏳ Link received. Waiting in queue…",
    },

    # === Forced Subscription (OP) ===
    "sub.required": {
        "ru": (
            "🔒 <b>Для использования бота, пожалуйста, подпишитесь на наши каналы:</b>\n\n"
            "После подписки нажмите кнопку <b>«✅ Я подписался»</b> ниже."
        ),
        "en": (
            "🔒 <b>To use this bot, please subscribe to our channels:</b>\n\n"
            "After subscribing, click the <b>«✅ I have subscribed»</b> button below."
        ),
    },
    "sub.check_btn": {
        "ru": "✅ Я подписался",
        "en": "✅ I have subscribed",
    },
    "sub.not_yet": {
        "ru": "❌ Вы ещё не подписались на все указанные каналы. Пожалуйста, подпишитесь и попробуйте снова.",
        "en": "❌ You haven't subscribed to all required channels yet. Please subscribe and try again.",
    },
    "sub.success": {
        "ru": "✅ Спасибо за подписку! Теперь вы можете отправлять ссылки для скачивания.",
        "en": "✅ Thank you for subscribing! You can now send links to download media.",
    },

    # === Admin panel ===
    "admin.no_access": {
        "ru": "⛔ У вас нет прав администратора.",
        "en": "⛔ You don't have administrator permissions.",
    },
    "admin.title": {
        "ru": "🛠 <b>Панель администратора:</b>",
        "en": "🛠 <b>Administrator Panel:</b>",
    },
    "admin.stats": {
        "ru": (
            "📊 <b>Статистика бота:</b>\n\n"
            "👥 Всего пользователей: <b>{total_users}</b>\n"
            "📢 Обязательных каналов (ОП): <b>{channels_count}</b>\n"
            "⚡ Задач в очереди: <b>{queue_len}</b>\n"
            "🔄 Версия yt-dlp: <code>{ytdlp_version}</code>"
        ),
        "en": (
            "📊 <b>Bot Statistics:</b>\n\n"
            "👥 Total users: <b>{total_users}</b>\n"
            "📢 Required channels: <b>{channels_count}</b>\n"
            "⚡ Queue length: <b>{queue_len}</b>\n"
            "🔄 yt-dlp version: <code>{ytdlp_version}</code>"
        ),
    },
    "admin.channels_title": {
        "ru": "📢 <b>Управление обязательными каналами (ОП):</b>\n\nТекущие каналы:",
        "en": "📢 <b>Manage Required Channels:</b>\n\nCurrent channels:",
    },
    "admin.no_channels": {
        "ru": "Каналы для проверки подписки пока не добавлены.",
        "en": "No required channels configured yet.",
    },
    "admin.btn_add_channel": {
        "ru": "➕ Добавить канал",
        "en": "➕ Add channel",
    },
    "admin.btn_del_channel": {
        "ru": "🗑 Удалить канал",
        "en": "🗑 Delete channel",
    },
    "admin.btn_stats": {
        "ru": "📊 Статистика",
        "en": "📊 Statistics",
    },
    "admin.btn_broadcast": {
        "ru": "✉️ Рассылка",
        "en": "✉️ Broadcast",
    },
    "admin.btn_back": {
        "ru": "◀️ Назад",
        "en": "◀️ Back",
    },
    "admin.btn_cancel": {
        "ru": "❌ Отмена",
        "en": "❌ Cancel",
    },
    "admin.prompt_channel_id": {
        "ru": (
            "➕ <b>Добавление канала:</b>\n\n"
            "Перешлите сообщение из канала или отправьте его ID / username (например, <code>@channel_username</code> или <code>-1001234567890</code>).\n\n"
            "<i>Примечание: Бот должен быть добавлен в этот канал администратором!</i>"
        ),
        "en": (
            "➕ <b>Add Channel:</b>\n\n"
            "Forward a message from the channel or send its ID / username (e.g. <code>@channel_username</code> or <code>-1001234567890</code>).\n\n"
            "<i>Note: The bot must be an administrator in this channel!</i>"
        ),
    },
    "admin.prompt_channel_title": {
        "ru": "Введите название кнопки для этого канала (например: <code>Наш основной канал</code>):",
        "en": "Enter the button title for this channel (e.g. <code>Our Main Channel</code>):",
    },
    "admin.prompt_channel_link": {
        "ru": "Введите ссылку-приглашение (например: <code>https://t.me/channel_username</code> или <code>https://t.me/+invitehash</code>):",
        "en": "Enter the invite link (e.g. <code>https://t.me/channel_username</code> or <code>https://t.me/+invitehash</code>):",
    },
    "admin.channel_added": {
        "ru": "✅ Канал «{title}» (<code>{channel_id}</code>) успешно добавлен в список обязательных подписок!",
        "en": "✅ Channel «{title}» (<code>{channel_id}</code>) successfully added to required subscriptions!",
    },
    "admin.channel_deleted": {
        "ru": "🗑 Канал успешно удален из обязательных подписок.",
        "en": "🗑 Channel successfully removed from required subscriptions.",
    },
    "admin.broadcast_prompt": {
        "ru": (
            "✉️ <b>Режим рассылки сообщений:</b>\n\n"
            "Отправьте сообщение (текст с форматированием, фото, видео или пересланный пост), которое хотите разослать всем пользователям бота.\n\n"
            "Для отмены отправьте /cancel или нажмите кнопку ниже."
        ),
        "en": (
            "✉️ <b>Broadcast Mode:</b>\n\n"
            "Send a message (formatted text, photo, video or forwarded post) that you want to send to all bot users.\n\n"
            "To cancel, send /cancel or click the button below."
        ),
    },
    "admin.broadcast_confirm": {
        "ru": (
            "📢 <b>Подтверждение рассылки:</b>\n\n"
            "👥 Получателей: <b>{count}</b> пользователей\n\n"
            "Отправить рассылку?"
        ),
        "en": (
            "📢 <b>Broadcast Confirmation:</b>\n\n"
            "👥 Recipients: <b>{count}</b> users\n\n"
            "Start sending broadcast?"
        ),
    },
    "admin.broadcast_started": {
        "ru": "🚀 Рассылка запущена в фоновом режиме. По завершении вы получите отчет.",
        "en": "🚀 Broadcast started in background. You will receive a report upon completion.",
    },
    "admin.broadcast_done": {
        "ru": (
            "📊 <b>Рассылка завершена!</b>\n\n"
            "✅ Успешно доставлено: <b>{success}</b>\n"
            "🚫 Заблокировали бота: <b>{blocked}</b>\n"
            "⚠️ Ошибок: <b>{errors}</b>\n"
            "⏱ Затрачено времени: <b>{duration:.1f} сек.</b>"
        ),
        "en": (
            "📊 <b>Broadcast completed!</b>\n\n"
            "✅ Successfully delivered: <b>{success}</b>\n"
            "🚫 Blocked bot: <b>{blocked}</b>\n"
            "⚠️ Errors: <b>{errors}</b>\n"
            "⏱ Time taken: <b>{duration:.1f}s</b>"
        ),
    },
    # === Video captions / подписи ===
    "caption.download_via": {
        "ru": "📥 Скачано через @{bot_username}",
        "en": "📥 Downloaded via @{bot_username}",
    },
    "caption.promo": {
        "ru": "📥 Скачать видео через бота @{bot_username}",
        "en": "📥 Download videos via bot @{bot_username}",
    },
    "caption.skip_ads": {
        "ru": "Пропуск рекламы:",
        "en": "Skip sponsor segments:",
    },
    "caption.after": {
        "ru": "после:",
        "en": "after:",
    },
    "caption.from": {
        "ru": "с",
        "en": "from",
    },
}

# Коды языков стран СНГ и русскоязычного пространства, которые мапятся на русский
CIS_LANGUAGES = frozenset({
    "ru",  # Русский (Россия, СНГ)
    "uk",  # Украинский
    "be",  # Белорусский
    "kk",  # Казахский
    "uz",  # Узбекский
    "ky",  # Кыргызский
    "tg",  # Таджикский
    "tk",  # Туркменский
    "az",  # Азербайджанский
    "hy",  # Армянский
    "ka",  # Грузинский
    "mo",  # Молдавский
    "ro",  # Румынский / Молдова
    "tt",  # Татарский
    "ba",  # Башкирский
    "cv",  # Чувашский
    "os",  # Осетинский
})


def detect_language(language_code: str | None) -> str:
    """Определяет язык интерфейса ('ru' или 'en').
    Для пользователей из стран СНГ (RU, UK, BE, KK, UZ, KY, TG, AZ, HY, KA и др.) — русский язык ('ru').
    Для всех остальных стран и языков мира — английский ('en').
    """
    if not language_code:
        return "ru"
    code = language_code.lower().strip().replace("-", "_").split("_")[0]
    if code in CIS_LANGUAGES:
        return "ru"
    return "en"


def t(key: str, lang: str = "ru", **kwargs: Any) -> str:
    """Returns translated string formatted with kwargs."""
    if lang not in {"ru", "en"}:
        lang = "ru"
    item = TRANSLATIONS.get(key)
    if not item:
        return key
    text = item.get(lang) or item.get("ru") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
