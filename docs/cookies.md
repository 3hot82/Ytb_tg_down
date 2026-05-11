# Cookies и Instagram

Некоторые сайты требуют cookies. Особенно часто это нужно для Instagram, TikTok, VK и YouTube age/private/private-like content.

Админ-команды доступны только пользователям из `ADMIN_IDS`.

## Загрузить cookies через Telegram

1. Отправьте боту команду:

```text
/cookies instagram
```

2. Прикрепите файл cookies.

Поддерживаемые имена:

```text
instagram.txt
tiktok.txt
vk.txt
youtube.txt
twitter.txt
cookies.txt
```

3. Проверить статус:

```text
/cookies
```

## Instagram stories

Команда:

```text
/stories username
/stories @username
/stories https://www.instagram.com/username?igsh=...
```

Обычная ссылка на Instagram-профиль сама по себе не запускает скачивание stories. Для stories нужна явная команда `/stories`.

Важно:

- для stories почти всегда нужны актуальные `instagram.txt` cookies;
- аккаунт, из которого экспортированы cookies, должен иметь доступ к stories;
- просроченные stories скачать нельзя.

## Auth-browser на сервере

Можно поднять серверный Chromium/noVNC, войти в Instagram и экспортировать cookies.

### 1. Настроить пароль

В `.env`:

```env
AUTH_BROWSER_USER=admin
AUTH_BROWSER_PASSWORD=replace_with_long_random_password
AUTH_BROWSER_PORT=33000
AUTH_BROWSER_URL=http://127.0.0.1:33000/
```

### 2. Запустить browser

```bash
docker compose --profile auth up -d auth-browser
```

### 3. Открыть Chromium

Локально на сервере:

```text
http://127.0.0.1:33000/
```

Если сервер удалён, используйте SSH tunnel:

```bash
ssh -L 33000:127.0.0.1:33000 user@server
```

После этого откройте на своём компьютере:

```text
http://127.0.0.1:33000/
```

### 4. Войти в Instagram

Внутри Chromium войдите в нужный аккаунт.

### 5. Экспортировать cookies

Отправьте боту:

```text
/cookies_export
```

Проверить:

```text
/cookies
```

## Если cookies database locked

Закройте вкладки сайта в auth-browser или перезапустите browser:

```bash
docker compose restart auth-browser
```

Потом снова:

```text
/cookies_export
```
