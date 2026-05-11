# Ytb TG Down — Telegram media downloader bot

Telegram-бот, который ловит ссылки на YouTube, TikTok, VK и Instagram, скачивает короткое видео/фото и отправляет файл обратно в чат.

Проект рассчитан на простой self-hosted запуск через Docker Compose.

## Что умеет

- Принимает ссылки из лички, групп и супергрупп.
- Скачивает видео через `yt-dlp`.
- Использует `gallery-dl` как fallback для фото, галерей и сложных постов.
- Использует `og:image`/`og:video` как последний лёгкий fallback.
- Отправляет `video`, `photo` или `document` в зависимости от результата.
- Ограничивает размер файла под стандартный Telegram Bot API: по умолчанию `50 MB`.
- Не оставляет в чате служебный мусор: временные ошибки удаляются.
- Поддерживает одну активную загрузку на чат.
- Умеет экспортировать cookies из серверного Chromium/noVNC для Instagram и других сайтов.
- Может обновлять runtime `yt-dlp` через отдельный `watcher`, не убивая активные скачивания.

## Стек

- Python 3.12
- `aiogram==3.26.0`
- `yt-dlp`
- `gallery-dl`
- `ffmpeg` / `ffprobe`
- Redis
- Docker Compose
- optional: `lscr.io/linuxserver/chromium` для server-side login через noVNC

## Ограничения

По умолчанию проект использует обычный cloud Telegram Bot API. Поэтому бот не должен пытаться отправлять файлы больше примерно `50 MB`.

Если нужен upload больших файлов, нужен отдельный официальный локальный Telegram Bot API server в `--local` режиме. В этом MVP он не включён.

Также намеренно запрещены:

- плейлисты;
- альбомы как единая batch-задача;
- live/upcoming видео;
- тяжёлое перекодирование.

Для видео приоритет такой:

1. MP4/H.264/AAC;
2. минимальное Telegram-compatible качество;
3. если нужно объединить video-only + audio-only, используется `ffmpeg` merge без перекодирования.

## Быстрый старт

### 1. Установите зависимости на сервере

Нужны:

- Docker;
- Docker Compose plugin;
- доступ контейнера к Telegram и сайтам-источникам.

Проверка:

```bash
docker --version
docker compose version
```

### 2. Склонируйте репозиторий

```bash
git clone https://github.com/3hot82/Ytb_tg_down.git
cd Ytb_tg_down
```

### 3. Создайте Telegram-бота

1. Откройте Telegram.
2. Напишите [@BotFather](https://t.me/BotFather).
3. Выполните:

```text
/newbot
```

4. Выберите имя и username бота.
5. BotFather выдаст токен вида:

```text
123456789:AA....
```

Это секрет. Не публикуйте его и не коммитьте в GitHub.

### 4. Настройте `.env`

```bash
cp .env.example .env
nano .env
```

Минимально нужно указать:

```env
BOT_TOKEN=ваш_токен_от_BotFather
ADMIN_IDS=ваш_telegram_user_id
```

`ADMIN_IDS` нужен для admin-only команд:

- `/login`
- `/cookies`
- `/cookies_export`

Свой numeric Telegram user id можно узнать через [@userinfobot](https://t.me/userinfobot) или похожего бота.

### 5. Запустите

```bash
docker compose up -d --build
```

Логи:

```bash
docker compose logs -f bot worker watcher
```

Остановка:

```bash
docker compose down
```

Полная остановка с удалением volumes, включая Redis queue/download cache/cookies/browser profile:

```bash
docker compose down -v
```

Используйте `-v` осторожно.

## Как сделать личного бота только для себя

Самый простой вариант:

1. Создайте бота через BotFather.
2. Никому не давайте username/token.
3. Не добавляйте бота в группы.
4. Напишите ему в личку `/start`.
5. Отправляйте ссылки прямо в личный чат с ботом.

В текущей версии бот технически отвечает любому, кто написал ему или в чат, где он состоит. Если нужен жёсткий allowlist пользователей/чатов, добавьте отдельную проверку `ALLOWED_USER_IDS`/`ALLOWED_CHAT_IDS` перед постановкой задачи в очередь.

## Как сделать, чтобы бот слышал группы и чаты

У Telegram-ботов есть BotFather-настройка **Privacy Mode**.

### Вариант A — безопаснее: Privacy Mode включён

Если privacy включён, в группах бот видит не все сообщения, а обычно только:

- команды `/start`, `/help` и т.п.;
- сообщения, где бота упомянули через `@username`;
- replies на сообщения бота;
- service events, если применимо.

Тогда пользователи должны отправлять ссылку примерно так:

```text
@your_bot_username https://youtube.com/...
```

или отвечать ссылкой на сообщение бота.

### Вариант B — удобнее: Privacy Mode выключен

Если хотите, чтобы бот автоматически ловил ссылки в группе:

1. Откройте [@BotFather](https://t.me/BotFather).
2. Выполните:

```text
/mybots
```

3. Выберите вашего бота.
4. `Bot Settings` → `Group Privacy`.
5. Нажмите `Turn off`.
6. Удалите бота из группы и добавьте снова, если Telegram не применил режим сразу.

После этого бот сможет видеть обычные текстовые сообщения в группе и ловить поддерживаемые ссылки.

### Какие права нужны в группе

Обычно достаточно добавить бота участником. Но чтобы бот мог удалять свои временные сообщения об ошибках, полезно дать право:

```text
Delete messages
```

Для отправки видео/фото специальных admin-прав обычно не нужно, если в группе разрешена отправка media.

## Cookies и Instagram login через серверный Chromium/noVNC

Некоторые сайты, особенно Instagram/TikTok/VK, могут требовать cookies. Проект поддерживает безопасную схему:

```text
вы открываете серверный Chromium
→ логинитесь в Instagram внутри него
→ browser profile остаётся в Docker volume
→ бот экспортирует cookies в /app/data/cookies/instagram.txt
→ yt-dlp/gallery-dl используют эти cookies
```

### Локальный запуск auth-browser

```bash
docker compose --profile auth up -d auth-browser
```

По умолчанию Chromium/noVNC доступен только локально на сервере:

```text
http://127.0.0.1:33000/
```

Это специально безопасный дефолт. Не открывайте этот порт напрямую в интернет без HTTPS и авторизации.

### Доступ с телефона

Рекомендуемая схема:

```text
телефон
  → HTTPS reverse proxy на VPS
  → Basic Auth + длинный случайный path
  → SSH reverse tunnel
  → auth-browser на 127.0.0.1:33000
```

Минимум защиты:

- HTTPS;
- Basic Auth;
- длинный случайный path;
- не публиковать URL;
- останавливать `auth-browser` после экспорта cookies.

Не монтируйте Docker socket внутрь бота. Это даёт боту почти root-доступ к хосту.

### Команды бота для cookies

Команды доступны только пользователям из `ADMIN_IDS`.

```text
/login
```

Показывает ссылку на auth-browser из `AUTH_BROWSER_URL`.

```text
/cookies_export
```

Экспортирует cookies из Chromium profile в Netscape-файл:

```text
/app/data/cookies/instagram.txt
```

```text
/cookies
```

Показывает наличие cookie-файлов.

После экспорта можно остановить браузер:

```bash
docker compose stop auth-browser
```

## Основные настройки `.env`

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
MAX_FILE_MB=50
MAX_DURATION_SECONDS=600
DOWNLOAD_TIMEOUT_SECONDS=600
CHAT_COOLDOWN_SECONDS=20
YTDLP_BIN=/opt/ytdlp/yt-dlp
DATA_DIR=/app/data
AUTH_BROWSER_URL=http://127.0.0.1:33000/
AUTH_BROWSER_USER=admin
AUTH_BROWSER_PASSWORD=replace_with_long_random_password
```

Файл `.env` должен оставаться локальным и не должен попадать в GitHub.

## Что нельзя публиковать

Не коммитьте:

- `.env`;
- `.env.*`, кроме `.env.example`;
- `.auth-proxy.env`;
- Telegram bot token;
- Basic Auth пароли;
- секретные reverse-proxy URL/path;
- cookies;
- browser profile;
- Redis/download volumes;
- логи;
- SSH keys.

В репозитории должен быть только пример конфигурации: `.env.example`.

## Проверка перед публикацией

```bash
python -m compileall app
docker compose config
```

Проверка, что секреты не попадут в Git:

```bash
git status --short
git check-ignore -v .env .auth-proxy.env || true
```

## Troubleshooting

### Бот не отвечает в группе

Проверьте Privacy Mode в BotFather.

Если privacy включён, пишите ссылку с упоминанием бота:

```text
@your_bot_username https://youtube.com/...
```

Если хотите автоловлю всех ссылок — выключите Group Privacy и пере-добавьте бота в группу.

### `Unauthorized` или bot polling не стартует

Проверьте `BOT_TOKEN` в `.env`.

### Файл больше 50 MB

Это лимит стандартного Telegram Bot API для загрузки ботом. Уменьшите `MAX_FILE_MB`/длительность или поднимайте официальный локальный Telegram Bot API server.

### Instagram не скачивается

1. Поднимите `auth-browser`.
2. Войдите в Instagram.
3. Выполните `/cookies_export`.
4. Проверьте `/cookies`.
5. Повторите скачивание.

### `/cookies_export` ругается на locked cookies database

Остановите браузер и повторите экспорт:

```bash
docker compose stop auth-browser
```

Затем в Telegram:

```text
/cookies_export
```

### Redis warning про `vm.overcommit_memory`

На Linux-хосте можно включить:

```bash
sudo sysctl vm.overcommit_memory=1
```

Для постоянной настройки добавьте в `/etc/sysctl.conf`:

```text
vm.overcommit_memory = 1
```

## Публикация в GitHub

Репозиторий:

```text
https://github.com/3hot82/Ytb_tg_down.git
```

Пример первого push:

```bash
git init
git add .
git status --short
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/3hot82/Ytb_tg_down.git
git push -u origin main
```

Перед `git add .` убедитесь, что `.env` и `.auth-proxy.env` игнорируются.

## Disclaimer

Используйте проект только для контента, который вы имеете право скачивать и пересылать. Соблюдайте правила сайтов-источников и законодательство вашей страны.
