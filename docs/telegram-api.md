# Telegram API: cloud и local server

Бот умеет работать в двух режимах. Переключение делается через `.env`.

## Cloud Bot API

Это стандартный режим Telegram Bot API. Ничего дополнительно поднимать не нужно.

```env
MAX_FILE_MB=50
TELEGRAM_API_BASE_URL=
TELEGRAM_API_FILE_URL=
```

Запуск:

```bash
docker compose up -d --build bot worker
```

Плюсы:

- проще всего;
- не нужны `api_id/api_hash`;
- меньше инфраструктуры.

Минус:

- cloud Bot API ограничивает upload бота, обычно около `50 MB`.

## Local Telegram Bot API server

Нужен для больших файлов. Используется официальный `telegram-bot-api` server.

### 1. Получить api_id/api_hash

Откройте:

```text
https://my.telegram.org/apps
```

Создайте приложение и получите:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
```

Важно:

- вход делается обычным пользовательским Telegram-аккаунтом;
- ботом получить `api_id/api_hash` нельзя;
- `api_id/api_hash` — секреты, не публикуйте их.

### 2. Настроить `.env`

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

### 3. Запустить local API

```bash
docker compose --profile local-api up -d telegram-bot-api
```

### 4. Перезапустить bot/worker

```bash
docker compose up -d --build bot worker
```

### 5. Проверить

```bash
docker compose ps telegram-bot-api bot worker
docker compose logs -f telegram-bot-api bot worker
```

Local API порт проброшен только на localhost:

```text
127.0.0.1:8081->8081
```

Наружу его открывать не нужно.

## Как подключение работает внутри Docker

В `.env` bot/worker получают:

```env
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot{token}/{method}
TELEGRAM_API_FILE_URL=http://telegram-bot-api:8081/file/bot{token}/{path}
```

`telegram-bot-api` видит скачанные файлы через общий volume:

```yaml
- downloads:/app/downloads
```

Это важно для local mode: worker скачивает файл, а local Bot API server должен иметь доступ к этому пути.

## Вернуться на cloud mode

В `.env`:

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
