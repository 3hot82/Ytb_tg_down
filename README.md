# Ytb TG Down

Self-hosted Telegram-бот для скачивания коротких видео и фото по ссылкам из Telegram.

Бот принимает ссылку на YouTube, TikTok, VK или Instagram, скачивает медиа через `yt-dlp`/`gallery-dl` и отправляет файл обратно в чат.

## Что умеет

- Работает в личных чатах, группах и супергруппах.
- Скачивает видео через `yt-dlp`.
- Использует `gallery-dl` как fallback для фото, галерей и сложных постов.
- Использует `og:image`/`og:video`, если основные загрузчики не справились.
- Отправляет результат как `video`, `photo` или `document`.
- Ограничивает размер файла под стандартный Telegram Bot API: по умолчанию `50 MB`.
- Не засоряет чат служебными сообщениями.
- Держит одну активную загрузку на чат.
- Поддерживает cookies для Instagram и других сайтов через серверный Chromium/noVNC.
- Может обновлять runtime `yt-dlp` отдельным watcher-сервисом.

## Из чего состоит

- Python 3.12
- `aiogram`
- `yt-dlp`
- `gallery-dl`
- `ffmpeg` / `ffprobe`
- Redis
- Docker Compose
- optional auth-browser: `lscr.io/linuxserver/chromium`

Сервисы Docker Compose:

- `bot` — слушает Telegram и ставит задачи в очередь;
- `worker` — скачивает медиа и отправляет результат;
- `watcher` — обновляет `yt-dlp` без остановки активных скачиваний;
- `redis` — очередь, locks и служебное состояние;
- `auth-browser` — optional Chromium/noVNC для входа в Instagram и экспорта cookies.

## Ограничения

По умолчанию используется обычный cloud Telegram Bot API, поэтому лимит отправляемого ботом файла — около `50 MB`.

В MVP намеренно не поддерживаются:

- плейлисты;
- batch-альбомы;
- live/upcoming видео;
- тяжёлое перекодирование.

Бот старается выбирать Telegram-compatible формат:

1. MP4/H.264/AAC;
2. минимальное подходящее качество;
3. если видео и аудио идут отдельно — merge через `ffmpeg` без перекодирования.

## Быстрый старт

### 1. Установите Docker

Нужны Docker и Docker Compose plugin.

Проверка:

```bash
docker --version
docker compose version
```

### 2. Скачайте проект

```bash
git clone https://github.com/3hot82/Ytb_tg_down.git
cd Ytb_tg_down
```

### 3. Создайте Telegram-бота

1. Откройте [@BotFather](https://t.me/BotFather).
2. Выполните команду:

```text
/newbot
```

3. Выберите имя и username бота.
4. Скопируйте токен, который выдаст BotFather.

Токен выглядит примерно так:

```text
123456789:AA...
```

Это секрет. Не публикуйте его и не коммитьте в GitHub.

### 4. Настройте `.env`

```bash
cp .env.example .env
nano .env
```

Минимально нужно заполнить:

```env
BOT_TOKEN=ваш_токен_от_BotFather
ADMIN_IDS=ваш_telegram_user_id
```

`ADMIN_IDS` нужен для админ-команд:

- `/login`
- `/cookies`
- `/cookies_export`

Свой numeric Telegram user id можно узнать через [@userinfobot](https://t.me/userinfobot) или похожего бота.

### 5. Запустите бота

```bash
docker compose up -d --build
```

Посмотреть логи:

```bash
docker compose logs -f bot worker watcher
```

Остановить:

```bash
docker compose down
```

Остановить и удалить volumes, включая Redis queue, downloads, cookies и browser profile:

```bash
docker compose down -v
```

Команду с `-v` используйте осторожно.

## Как пользоваться

### В личном чате

1. Напишите боту `/start`.
2. Отправьте ссылку на поддерживаемый сайт.
3. Дождитесь файла в ответ.

Пример:

```text
https://www.youtube.com/watch?v=...
```

### В группе

Добавьте бота в группу и отправьте ссылку.

Если у бота включён Telegram Privacy Mode, он видит не все сообщения в группе. В таком режиме отправляйте ссылку с упоминанием бота:

```text
@your_bot_username https://www.youtube.com/watch?v=...
```

или reply на сообщение бота.

Если хотите, чтобы бот сам ловил обычные ссылки в группе, выключите Privacy Mode.

## Как выключить Privacy Mode в BotFather

1. Откройте [@BotFather](https://t.me/BotFather).
2. Выполните:

```text
/mybots
```

3. Выберите вашего бота.
4. Откройте `Bot Settings`.
5. Откройте `Group Privacy`.
6. Нажмите `Turn off`.
7. Если бот уже был в группе, удалите его из группы и добавьте снова.

После этого бот сможет видеть обычные текстовые сообщения в группе и ловить ссылки без упоминания.

## Какие права нужны боту в группе

Для базовой работы обычно достаточно добавить бота участником группы.

Полезно дать право:

```text
Delete messages
```

Оно нужно, чтобы бот мог удалять свои временные сообщения об ошибках.

Если в группе запрещена отправка media, разрешите боту отправлять фото/видео/документы.

## Личный бот только для себя

Самый простой вариант:

1. Создайте бота через BotFather.
2. Не добавляйте его в публичные группы.
3. Никому не передавайте username и токен.
4. Пишите ссылки только в личный чат с ботом.

Важно: текущая версия не содержит allowlist для обычных скачиваний. Если бот добавлен в группу или кто-то нашёл его username и написал ему, он сможет поставить задачу на скачивание. Для строго приватного режима добавьте проверку `ALLOWED_USER_IDS`/`ALLOWED_CHAT_IDS` в обработчик ссылок.

## Cookies и Instagram

Instagram, TikTok, VK и другие сайты иногда требуют авторизацию. Основной удобный способ — экспортировать `cookies.txt` на своём устройстве и отправить файл боту.

Схема такая:

```text
вы логинитесь в Instagram/TikTok/VK в своём браузере
→ экспортируете cookies в Netscape cookies.txt
→ отправляете файл боту в Telegram
→ бот сохраняет cookies в /app/data/cookies/*.txt
→ yt-dlp/gallery-dl используют эти cookies
```

### Загрузка cookies через Telegram

Команды доступны только пользователям из `ADMIN_IDS`.

```text
/cookies_upload
```

После команды отправьте cookies-файл документом. Бот умеет определить платформу по доменам внутри файла, даже если имя файла случайное. Но для надёжности лучше назвать файл по платформе:

```text
instagram.txt
tiktok.txt
vk.txt
youtube.txt
twitter.txt
cookies.txt
```

Если в одном файле cookies сразу от нескольких сайтов, бот сохранит его как общий `cookies.txt`. Такой файл используется как fallback, если для конкретной платформы нет своего файла.

Можно явно выбрать платформу:

```text
/cookies_upload_instagram
/cookies_upload_tiktok
/cookies_upload_vk
```

После этого бот попросит прислать файл и сохранит его в нужное место.

Проверить статус:

```text
/cookies
```

### Для каких сайтов лучше иметь cookies

Рекомендуемый минимум:

- `instagram.txt` — Instagram чаще всего требует cookies для постов, reels, фото, приватности, антибот-проверок.
- `tiktok.txt` — TikTok часто меняет ограничения и может требовать сессию/региональные cookies.
- `vk.txt` — VK/VK Video может требовать авторизацию для части видео и групп.

Опционально:

- `youtube.txt` — обычно YouTube работает без cookies, но они нужны для age-restricted/private/unlisted/member-контента, CAPTCHA/soft-block и иногда rate-limit. yt-dlp предупреждает, что аккаунт может получить временные ограничения, поэтому лучше использовать отдельный/неосновной аккаунт.
- `twitter.txt` — для X/Twitter cookies нужны для части медиа, NSFW, ограниченного или login-only контента.
- `cookies.txt` — общий fallback-файл, если не хотите разделять по сайтам.

По документации yt-dlp cookies должны быть в Mozilla/Netscape формате, первая строка обычно `# Netscape HTTP Cookie File` или `# HTTP Cookie File`. gallery-dl тоже умеет использовать cookies-файлы и передавать их загрузчикам.

### Где взять cookies.txt

На ПК в обычном Google Chrome расширения есть. Можно установить расширение вроде `Cookie-Editor` или `Get cookies.txt LOCALLY`, открыть нужный сайт, войти в аккаунт и экспортировать cookies в формат Netscape `cookies.txt`.

На Android обычный Chrome не поддерживает расширения. Для телефона обычно удобнее:

- Kiwi Browser;
- Lemur Browser;
- другой Chromium-браузер с поддержкой Chrome extensions.

В таком браузере можно поставить `Cookie-Editor` / `Get cookies.txt LOCALLY`, войти в Instagram и экспортировать `cookies.txt`.

На iPhone/iOS экспорт cookies из браузера сильно ограничен. Надёжнее сделать экспорт один раз с ПК/Mac или Android-браузера с расширениями.

### Optional: auth-browser на сервере

В проекте также есть optional `auth-browser`: серверный Chromium/noVNC. Это запасной вариант, если удобнее логиниться на сервере. На телефонах web-VNC может работать нестабильно, поэтому для мобильного сценария лучше использовать upload cookies-файла.

### Запуск auth-browser

```bash
docker compose --profile auth up -d auth-browser
```

По умолчанию браузер доступен только локально на сервере:

```text
http://127.0.0.1:33000/
```

Это безопасный дефолт. Не открывайте этот порт напрямую в интернет без HTTPS и авторизации.

### Доступ к auth-browser с телефона

Если всё-таки используете удалённый браузер, рекомендуемая схема:

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

Не монтируйте Docker socket внутрь бота. Это даёт боту слишком много прав на хосте.

### Команды для auth-browser

```text
/login
```

Показывает ссылку на auth-browser из `AUTH_BROWSER_URL`.

```text
/cookies_export
```

Экспортирует Instagram cookies из Chromium profile в:

```text
/app/data/cookies/instagram.txt
```

После экспорта можно остановить браузер:

```bash
docker compose stop auth-browser
```

## Основные настройки

Все настройки лежат в `.env`.

Самые важные:

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

Файл `.env` должен оставаться локальным. Не публикуйте его.

## Что нельзя публиковать

Не коммитьте и не выкладывайте:

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

## Проверка установки

Проверить Python-код:

```bash
python -m compileall app
```

Проверить Docker Compose config:

```bash
docker compose config
```

Проверить запущенные сервисы:

```bash
docker compose ps
```

## Troubleshooting

### Бот не отвечает в группе

Проверьте Privacy Mode в BotFather.

Если privacy включён, отправляйте ссылку с упоминанием:

```text
@your_bot_username https://youtube.com/...
```

Если хотите автоловлю ссылок — выключите Group Privacy и пере-добавьте бота в группу.

### Бот не стартует или пишет Unauthorized

Проверьте `BOT_TOKEN` в `.env`.

После изменения `.env` перезапустите сервис:

```bash
docker compose up -d bot
```

### Файл больше 50 MB

Это лимит стандартного Telegram Bot API. Уменьшите `MAX_FILE_MB`, ограничьте длительность или используйте официальный локальный Telegram Bot API server.

### Instagram не скачивается

1. Поднимите auth-browser:

```bash
docker compose --profile auth up -d auth-browser
```

2. Войдите в Instagram внутри серверного Chromium.
3. Отправьте боту:

```text
/cookies_export
```

4. Проверьте:

```text
/cookies
```

5. Повторите скачивание ссылки.

### `/cookies_export` ругается на locked cookies database

Остановите браузер и повторите экспорт:

```bash
docker compose stop auth-browser
```

Затем отправьте боту:

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

## Disclaimer

Используйте проект только для контента, который вы имеете право скачивать и пересылать. Соблюдайте правила сайтов-источников и законодательство вашей страны.
