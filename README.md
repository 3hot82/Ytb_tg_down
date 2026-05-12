# Ytb TG Down

Self-hosted Telegram-бот для скачивания медиа по ссылкам.

Бот принимает ссылку в Telegram, скачивает медиа через `yt-dlp` / `gallery-dl` и отправляет файл обратно в чат.

## Проверено

- ✅ YouTube / youtu.be
- ✅ TikTok
- ✅ Instagram posts / reels / stories

## Возможности
- YouTube скачивается через `yt-dlp` с предохранителем по размеру файла до скачивания.
- Режимы кодека YouTube через `VIDEO_CODEC_MODE`: `mp4`, `av1`, `vp9`, `auto`.
- AV1/VP9 скачиваются как `video-only + audio` и объединяются без перекодировки, чтобы уменьшить размер файла.
- Выбор аудиодорожки YouTube через `YOUTUBE_MULTI_AUDIO=true` и `YOUTUBE_AUDIO_LANGUAGE=ru`; если языка нет, бот берёт оригинальную/default дорожку.
- SponsorBlock helper через `YOUTUBE_SPONSORBLOCK_CAPTION=true`: видео не режется, в подпись добавляются кликабельные таймкоды конца рекламных/саморекламных сегментов.
- Для YouTube-видео используется оригинальная обложка от `yt-dlp` как Telegram `cover`.
- Instagram stories через `/stories username` или `/stories https://instagram.com/username?...`.
- Cookies можно загрузить файлом или экспортировать через серверный Chromium.
- Для больших файлов поддерживается официальный local Telegram Bot API server.
- Повторные ссылки отправляются через Telegram `file_id` cache без повторного скачивания; TTL кэша — 90 дней. Cache key учитывает кодек и выбранный язык аудио.
- Очередь с лимитами pending задач: до 5 ссылок в одном сообщении, до 20 задач на чат и до 5 на пользователя.
- При старте worker может очищать старую очередь (`CLEAR_QUEUE_ON_WORKER_START=true`), чтобы после включения не начать качать пачку старых ссылок.

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

## Основные настройки `.env`

### YouTube codec

```env
VIDEO_CODEC_MODE=mp4
```

- `mp4` — H.264/AAC, максимальная совместимость Telegram/iPhone/Mac, но файлы больше.
- `av1` — AV1 + AAC в `.mp4` без перекодировки, обычно сильно меньше.
- `vp9` / `av9` — VP9/Opus в `.webm`, компактно, но хуже совместимость Telegram.
- `auto` — сейчас ведёт себя как `mp4`.

### YouTube audio language

```env
YOUTUBE_MULTI_AUDIO=false
YOUTUBE_AUDIO_LANGUAGE=ru
YOUTUBE_PLAYER_CLIENT=web_embedded
```

- `YOUTUBE_MULTI_AUDIO=false` — брать оригинальную/default аудиодорожку.
- `YOUTUBE_MULTI_AUDIO=true` — предпочитать язык из `YOUTUBE_AUDIO_LANGUAGE`.
- Если выбранного языка нет, бот автоматически падает обратно на original/default audio.
- `YOUTUBE_PLAYER_CLIENT=web_embedded` помогает `yt-dlp` увидеть дополнительные дубляжи YouTube, например русскую дорожку у части роликов.

### SponsorBlock timestamps

```env
YOUTUBE_SPONSORBLOCK_CAPTION=false
YOUTUBE_SPONSORBLOCK_CATEGORIES=sponsor,selfpromo,interaction
```

- `false` — SponsorBlock не используется.
- `true` — бот не режет и не перекодирует видео, а добавляет в подпись таймкоды, куда нажать для пропуска сегмента.
- Пример подписи:

```text
Пропуск рекламы:
⏭ 1:29 — после: самореклама (с 1:07)
⏭ 3:10 — после: спонсорская вставка (с 1:34)
```

Доступные категории: `sponsor`, `selfpromo`, `interaction`, `intro`, `outro`, `preview`, `music_offtopic`, `filler`.

### Queue/cache guards

```env
CLEAR_QUEUE_ON_WORKER_START=true
MEDIA_CACHE_TTL_SECONDS=7776000
```

- `CLEAR_QUEUE_ON_WORKER_START=true` очищает старую очередь при старте worker, чтобы не начать скачивать старые ссылки после включения.
- `MEDIA_CACHE_TTL_SECONDS` задаёт TTL Telegram `file_id` cache; по умолчанию 90 дней.

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
