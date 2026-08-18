# Команды бота

## Пользовательские

### Ссылка на медиа

Просто отправьте ссылку:

```text
https://youtu.be/...
https://www.tiktok.com/...
https://www.instagram.com/reel/...
https://vk.com/...
```

### Instagram stories

```text
/stories username
/stories @username
/stories https://www.instagram.com/username?igsh=...
```

### Смена языка (RU / EN)

```text
/lang
/language
```

Позволяет переключить язык интерфейса бота между Русским 🇷🇺 и English 🇬🇧.

### Справка

```text
/help
```

### Принудительно скачать заново

Telegram `file_id` cache хранится 90 дней по умолчанию (`MEDIA_CACHE_TTL_SECONDS=7776000`).

Если бот отправил битый/неотображаемый файл из cache, обойдите кэш:

```text
/redownload https://youtu.be/...
```

После успешной отправки кэш для этой ссылки обновится новым `file_id`.

## Админские

Админские команды доступны только пользователям из `ADMIN_IDS`.

### Панель администратора

```text
/admin
```

Интерактивная админ-панель с кнопками:
- 📊 **Статистика**: количество уникальных пользователей, активные задачи в очереди, каналы ОП, версия `yt-dlp`.
- 📢 **Управление обязательными каналами (ОП)**: добавление каналов (по ID, названию и инвайт-ссылке) и удаление.
- ✉️ **Рассылка (Broadcast)**: отправка текста, фото, видео или пересланных сообщений всем пользователям с защитой от лимитов (троттлинг).
- `/cancel`: отмена текущей операции добавления канала или рассылки.

### Cookies status

```text
/cookies
```

### Загрузка cookies файлом

```text
/cookies instagram
/cookies tiktok
/cookies vk
/cookies youtube
```

После команды отправьте `.txt` файл cookies.

### Экспорт cookies из auth-browser

```text
/cookies_export
```

### Login browser link

```text
/login
```

Используется для получения ссылки на auth-browser, если он настроен.
