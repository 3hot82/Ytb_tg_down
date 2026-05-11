# Ytb TG Down

Self-hosted Telegram-бот для скачивания медиа по ссылкам.

Бот принимает ссылки из Telegram, скачивает медиа через `yt-dlp` / `gallery-dl` и отправляет результат обратно в чат.

Поддерживается:

- YouTube / youtu.be — только `yt-dlp`;
- TikTok;
- VK / VK Video;
- Instagram posts/reels/stories;
- cookies для сайтов, где нужна авторизация;
- локальный Telegram Bot API server для файлов больше cloud-лимита.

## Состав

Docker Compose сервисы:

- `bot` — принимает сообщения Telegram и ставит задачи в Redis;
- `worker` — скачивает и отправляет медиа;
- `watcher` — обновляет runtime `yt-dlp`;
- `redis` — очередь и locks;
- `telegram-bot-api` — optional локальный Bot API server для больших файлов.
- `auth-browser` — optional Chromium/noVNC для входа в Instagram и экспорта cookies.

## Быстрый старт

```bash
git clone https://github.com/3hot82/Ytb_tg_down.git
cd Ytb_tg_down
cp .env.example .env
nano .env
```

Минимум для запуска через обычный cloud Telegram Bot API:

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
MAX_FILE_MB=50
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=
```

Где взять:

- `BOT_TOKEN` — у [@BotFather](https://t.me/BotFather) через `/newbot`;
- `ADMIN_IDS` — numeric Telegram user id, например через `@userinfobot`.

Запуск:

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
docker compose logs -f bot worker
```

Остановка:

```bash
docker compose down
```

Остановка с удалением volumes, включая cookies/profile/downloads/Redis:

```bash
docker compose down -v
```

`down -v` используйте осторожно.

## Режимы Telegram API

Бот умеет работать в двух режимах. Переключение делается через `.env`.

### 1. Cloud Bot API, стандартный режим

Это обычный API Telegram. Ничего дополнительно поднимать не нужно.

```env
MAX_FILE_MB=50
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=
```

Плюсы:

- проще всего;
- не нужны `api_id/api_hash`;
- меньше локальной инфраструктуры.

Минус:

- загрузка ботом ограничена cloud Bot API, обычно около `50 MB`.

### 2. Local Telegram Bot API server, большие файлы

Нужен, чтобы бот мог отправлять большие файлы через официальный локальный `telegram-bot-api` server.

Получите `api_id` и `api_hash`:

```text
https://my.telegram.org/apps
```

Важно: вход делается обычным пользовательским Telegram-аккаунтом. Ботом получить `api_id/api_hash` нельзя.

В `.env`:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_API_PORT=8081
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot{token}/{method}
TELEGRAM_API_FILE_URL=http://telegram-bot-api:8081/file/bot{token}/{path}
MAX_FILE_MB=2000
MAX_DURATION_SECONDS=7200
DOWNLOAD_TIMEOUT_SECONDS=3600
JOB_TTL_SECONDS=7200
ACTIVE_JOB_IDLE_TIMEOUT_SECONDS=7200
```

Запустить локальный сервер:

```bash
docker compose --profile local-api up -d telegram-bot-api
```

Перезапустить bot/worker, чтобы они начали ходить в локальный API:

```bash
docker compose up -d --build bot worker
```

Проверка:

```bash
docker compose ps telegram-bot-api bot worker
docker compose logs -f telegram-bot-api bot worker
```

Отключить локальный сервер и вернуться на cloud API:

```env
MAX_FILE_MB=50
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=
```

Затем:

```bash
docker compose up -d --build bot worker
docker compose stop telegram-bot-api
```

Примечания:

- порт local API проброшен только на `127.0.0.1:${TELEGRAM_BOT_API_PORT:-8081}`;
- наружу его открывать не нужно;
- volume `downloads` подключён к `telegram-bot-api`, чтобы local API видел файлы worker напрямую;
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и `BOT_TOKEN` — секреты, не коммитьте их.

## Как пользоваться

Отправьте ссылку боту:

```text
https://youtu.be/...
https://www.tiktok.com/...
https://vk.com/...
https://www.instagram.com/reel/...
```

Бот поставит задачу в очередь, скачает медиа и отправит файл ответом на сообщение.

Для Instagram stories:

```text
/stories username
/stories @username
/stories https://www.instagram.com/username?igsh=...
```

Обычная ссылка на Instagram-профиль сама по себе не запускает скачивание stories. Для stories нужна явная команда `/stories`.

## Группы

Чтобы бот видел обычные ссылки в группе, выключите Privacy Mode:

1. откройте [@BotFather](https://t.me/BotFather);
2. `/mybots`;
3. выберите бота;
4. `Bot Settings` → `Group Privacy` → `Turn off`;
5. удалите бота из группы и добавьте снова.

Если Privacy Mode включён, пишите ссылку с упоминанием:

```text
@your_bot_username https://youtube.com/...
```

## Cookies и Instagram

Некоторые сайты требуют cookies. Особенно часто это Instagram, TikTok, VK и YouTube age/private content.

Админ-команды доступны только пользователям из `ADMIN_IDS`.

### Загрузить cookies через Telegram

Отправьте боту:

```text
/cookies instagram
```

Потом прикрепите файл `instagram.txt` или `cookies.txt`.

Проверить:

```text
/cookies
```

Поддерживаемые имена:

```text
instagram.txt
tiktok.txt
vk.txt
youtube.txt
twitter.txt
cookies.txt
```

### Auth-browser для cookies

Можно поднять серверный Chromium:

```bash
docker compose --profile auth up -d auth-browser
```

Открыть:

```text
http://127.0.0.1:33000/
```

Войти в нужный сайт, затем отправить боту:

```text
/cookies_export
```

Auth-browser защищайте паролем через `.env`:

```env
AUTH_BROWSER_USER=admin
AUTH_BROWSER_PASSWORD=replace_with_long_random_password
AUTH_BROWSER_URL=http://127.0.0.1:33000/
```

## Основные настройки `.env`

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
REDIS_URL=redis://redis:6379/0
QUEUE_NAME=media_jobs

MAX_FILE_MB=50
MAX_DURATION_SECONDS=600
DOWNLOAD_TIMEOUT_SECONDS=600
JOB_TTL_SECONDS=1800
CHAT_COOLDOWN_SECONDS=20
ACTIVE_JOB_IDLE_TIMEOUT_SECONDS=1800

TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_BOT_API_PORT=8081
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=

YTDLP_UPDATE_INTERVAL_SECONDS=21600
YTDLP_UPDATE_POLL_SECONDS=10
YTDLP_RESTART_AFTER_UPDATE=false
YTDLP_BIN=/opt/ytdlp/yt-dlp

DATA_DIR=/app/data
LOG_LEVEL=INFO
```

## Проверка перед публикацией / деплоем

```bash
python -m compileall app
docker compose config
docker compose ps
```

Проверить, что секреты не попали в git:

```bash
git status --short
git ls-files .env
grep -R "BOT_TOKEN\|TELEGRAM_API_HASH\|AUTH_BROWSER_PASSWORD" . \
  --exclude=.env --exclude-dir=.git
```

`git ls-files .env` не должен выводить `.env`.

## Troubleshooting

### Бот не стартует или пишет Unauthorized

Проверьте `BOT_TOKEN` и перезапустите:

```bash
docker compose up -d --build bot
```

### Файл больше 50 MB

В cloud-режиме это ожидаемо. Включите local Telegram Bot API server и поднимите `MAX_FILE_MB`.

### Local Bot API работает, но бот молчит при больших файлах

Проверьте, что `telegram-bot-api` запущен и видит volume `downloads`:

```bash
docker compose ps telegram-bot-api worker
docker compose logs -f telegram-bot-api worker
```

В `docker-compose.yml` у `telegram-bot-api` должен быть volume:

```yaml
- downloads:/app/downloads
```

### Instagram не скачивается

Проверьте cookies:

```text
/cookies
```

Если cookies старые — обновите через `/cookies instagram` или auth-browser + `/cookies_export`.

### Redis warning про `vm.overcommit_memory`

На Linux-хосте можно включить:

```bash
sudo sysctl vm.overcommit_memory=1
```

Постоянно:

```bash
echo 'vm.overcommit_memory=1' | sudo tee /etc/sysctl.d/99-redis-overcommit.conf
sudo sysctl --system
```

## Что нельзя публиковать

Не коммитьте и не выкладывайте:

- `.env` и `.env.*`, кроме `.env.example`;
- `BOT_TOKEN`;
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`;
- cookies;
- browser profile;
- Redis/download volumes;
- логи с токенами;
- SSH keys и пароли.

## Disclaimer

Используйте только для контента, который вы имеете право скачивать и пересылать. Соблюдайте правила Telegram и сайтов-источников.
