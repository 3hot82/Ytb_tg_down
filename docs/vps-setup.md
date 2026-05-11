# Установка на VPS / виртуалку

Инструкция для чистого Linux-сервера. Проверялось на Ubuntu/Debian-подобной VPS.

## 1. Подготовить сервер

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
```

Установить Docker:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Перезайдите в SSH-сессию, затем проверьте:

```bash
docker --version
docker compose version
```

## 2. Скачать проект

```bash
git clone https://github.com/3hot82/Ytb_tg_down.git
cd Ytb_tg_down
cp .env.example .env
nano .env
```

## 3. Создать Telegram-бота

1. Откройте [@BotFather](https://t.me/BotFather).
2. Выполните `/newbot`.
3. Скопируйте токен.
4. Запишите его в `.env`:

```env
BOT_TOKEN=123456:replace_me
ADMIN_IDS=123456789
```

`ADMIN_IDS` — numeric Telegram user id. Его можно узнать через `@userinfobot`.

## 4. Выбрать режим Telegram API

### Обычный cloud mode

Подходит для старта. Лимит загрузки ботом обычно около `50 MB`.

```env
MAX_FILE_MB=50
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=
```

### Local Telegram Bot API для больших файлов

Если нужны файлы больше cloud-лимита, настройте local server по инструкции:

- [Режимы Telegram API](telegram-api.md)

## 5. Запустить

```bash
docker compose up -d --build
```

Проверить:

```bash
docker compose ps
docker compose logs -f bot worker
```

## 6. Проверить работу

Отправьте боту ссылку:

```text
https://youtu.be/...
```

Для Instagram может понадобиться cookies:

- [Cookies и Instagram](cookies.md)

## 7. Обновление

```bash
git pull
docker compose up -d --build
```

## 8. Остановка

```bash
docker compose down
```

Удалить все volumes, включая cookies/profile/downloads/Redis:

```bash
docker compose down -v
```

`down -v` используйте осторожно.
