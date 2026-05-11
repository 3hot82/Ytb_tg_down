# Troubleshooting

## Бот не стартует или пишет Unauthorized

Проверьте `BOT_TOKEN` в `.env`.

После изменения `.env`:

```bash
docker compose up -d --build bot
```

Логи:

```bash
docker compose logs -f bot
```

## Бот не отвечает в группе

Проверьте Privacy Mode в BotFather.

Чтобы бот видел обычные ссылки в группе:

1. откройте [@BotFather](https://t.me/BotFather);
2. `/mybots`;
3. выберите бота;
4. `Bot Settings` → `Group Privacy` → `Turn off`;
5. удалите бота из группы и добавьте снова.

Если Privacy Mode включён, пишите ссылку с упоминанием:

```text
@your_bot_username https://youtube.com/...
```

## Файл больше 50 MB

В cloud mode это ожидаемо. Включите local Telegram Bot API server:

- [Telegram API: cloud и local server](telegram-api.md)

## Local Bot API включён, но бот молчит на больших файлах

Проверьте сервисы:

```bash
docker compose ps telegram-bot-api bot worker
docker compose logs -f telegram-bot-api worker
```

Проверьте `.env`:

```env
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot{token}/{method}
TELEGRAM_API_FILE_URL=http://telegram-bot-api:8081/file/bot{token}/{path}
```

Проверьте, что в `docker-compose.yml` у `telegram-bot-api` есть общий volume:

```yaml
- downloads:/app/downloads
```

## Instagram не скачивается

Проверьте cookies:

```text
/cookies
```

Если cookies старые — обновите через:

```text
/cookies instagram
```

или через auth-browser:

```bash
docker compose --profile auth up -d auth-browser
```

Потом:

```text
/cookies_export
```

## yt-dlp устарел

Есть watcher-сервис, который обновляет runtime `yt-dlp`.

Проверить watcher:

```bash
docker compose ps watcher
docker compose logs -f watcher
```

Полная пересборка:

```bash
docker compose up -d --build worker watcher
```

## Redis warning про vm.overcommit_memory

На Linux-хосте:

```bash
sudo sysctl vm.overcommit_memory=1
```

Постоянно:

```bash
echo 'vm.overcommit_memory=1' | sudo tee /etc/sysctl.d/99-redis-overcommit.conf
sudo sysctl --system
```

## Проверки перед issue / деплоем

```bash
python -m compileall app
docker compose config
docker compose ps
docker compose logs --tail 100 bot worker
```
