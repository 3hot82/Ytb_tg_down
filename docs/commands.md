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

## Админские

Админские команды доступны только пользователям из `ADMIN_IDS`.

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
