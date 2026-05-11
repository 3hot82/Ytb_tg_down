# Ytb TG Down

Self-hosted Telegram-бот для скачивания медиа по ссылкам.

Бот принимает ссылку в Telegram, скачивает медиа через `yt-dlp` / `gallery-dl` и отправляет файл обратно в чат.

## Проверено

- ✅ YouTube / youtu.be
- ✅ TikTok
- ✅ Instagram posts / reels / stories
- ✅ VK / VK Video
- ✅ Telegram Bot API cloud mode
- ✅ Local Telegram Bot API server для файлов больше cloud-лимита

## Возможности

- YouTube скачивается только через `yt-dlp`.
- Instagram stories через `/stories username` или `/stories https://instagram.com/username?...`.
- Cookies можно загрузить файлом или экспортировать через серверный Chromium.
- Для больших файлов поддерживается официальный local Telegram Bot API server.
- Для YouTube-видео используется оригинальная обложка от `yt-dlp` как Telegram `cover`.
- Повторные ссылки отправляются через Telegram `file_id` cache без повторного скачивания; TTL кэша — 90 дней.
- В чате держится одна активная загрузка, чтобы не забить сервер.

## Быстрый старт

```bash
git clone https://github.com/3hot82/Ytb_tg_down.git
cd Ytb_tg_down
cp .env.example .env
nano .env
```

Минимум в `.env`:

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
```

Запуск:

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs -f bot worker
```

Отправьте боту ссылку:

```text
https://youtu.be/...
https://www.tiktok.com/...
https://www.instagram.com/reel/...
```

## Документация

- [Установка на VPS / виртуалку](docs/vps-setup.md)
- [Режимы Telegram API: cloud и local server](docs/telegram-api.md)
- [Cookies и Instagram](docs/cookies.md)
- [Команды бота](docs/commands.md)
- [Troubleshooting](docs/troubleshooting.md)

## Основные сервисы

- `bot` — принимает сообщения Telegram и ставит задачи в Redis;
- `worker` — скачивает и отправляет медиа;
- `watcher` — обновляет runtime `yt-dlp`;
- `redis` — очередь и locks;
- `telegram-bot-api` — optional local Bot API server;
- `auth-browser` — optional Chromium/noVNC для cookies.

## Безопасность

Не публикуйте:

- `.env` и `.env.*`, кроме `.env.example`;
- `BOT_TOKEN`;
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`;
- cookies;
- browser profile;
- Redis/download volumes;
- логи с токенами.

## Disclaimer

Используйте только для контента, который вы имеете право скачивать и пересылать. Соблюдайте правила Telegram и сайтов-источников.
