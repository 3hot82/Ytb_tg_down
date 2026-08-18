# Управление Cookies для бота

Некоторые платформы (YouTube для 18+/ограниченных видео, Instagram для закрытых постов/Reels/Stories, TikTok, VK) требуют cookies для стабильной загрузки.

Все команды управления cookies доступны **только администраторам** из `ADMIN_IDS`.

---

## 📱 Экспорт cookies с телефона (Android / iOS)

Самый быстрый и удобный способ — использовать браузер с поддержкой расширений на телефоне.

### Вариант 1: Android (Kiwi Browser или Lemur Browser)

1. **Установите браузер:**
   - Скачайте **Kiwi Browser** или **Lemur Browser** из Google Play (они поддерживают расширения Chrome).
2. **Установите расширение для cookies:**
   - Откройте в браузере Chrome Web Store и найдите расширение **Cookie-Editor** (или **Get cookies.txt LOCALLY**).
   - Нажмите *Установить / Добавить в Chrome*.
3. **Войдите на нужный сайт:**
   - Откройте `https://www.youtube.com` или `https://www.instagram.com` и выполните вход в свой аккаунт.
4. **Экспортируйте cookies:**
   - Нажмите меню браузера `⋮` (в правом верхнем углу).
   - В самом низу списка выберите **Cookie-Editor**.
   - Нажмите кнопку **Export** (Экспорт) -> выберите **Export as Netscape / cookies.txt** (или скопируйте текст).
   - Сохраните файл как `youtube.txt` (для YouTube) или `instagram.txt` (для Instagram).
5. **Отправьте файл боту:**
   - Откройте Telegram и отправьте полученный файл документом в диалог с ботом.
   - Бот ответит: `✅ Cookies для youtube сохранены`.

---

### Вариант 2: Компьютер (Chrome / Firefox / Edge)

1. Установите расширение **Cookie-Editor** или **Get cookies.txt LOCALLY** в ваш браузер на ПК.
2. Войдите в свой аккаунт на YouTube или Instagram.
3. Откройте расширение -> нажмите **Export as cookies.txt**.
4. Сохраните файл под именем:
   - `youtube.txt`
   - `instagram.txt`
   - `tiktok.txt`
   - `vk.txt`
5. Отправьте файл боту в Telegram как обычный документ.

---

## 🤖 Команды бота в Telegram

| Команда | Описание |
|---|---|
| `/cookies` | Показать статус cookies по всем платформам (наличие и размер файлов) |
| `/cookies_upload_instagram` | Подготовить бота к приёму cookies для Instagram (если файл называется не `instagram.txt`) |
| `/cookies_upload_tiktok` | Подготовить бота к приёму cookies для TikTok |
| `/cookies_upload_vk` | Подготовить бота к приёму cookies для VK |
| `/redownload <url>` | Принудительно перекачать видео заново, минуя кэш |
| `/stories <username>` | Скачать актуальные Stories пользователя Instagram (требуются `instagram.txt` cookies) |

---

## 🔍 Как проверить, что cookies работают

1. Отправьте боту команду:
   ```text
   /cookies
   ```
   Ответ бота:
   ```text
   Cookies status:
   instagram: ok, 1420 bytes
   youtube: ok, 3810 bytes
   tiktok: missing
   vk: missing
   ```
2. Отправьте боту ссылку на видео с возрастным ограничением или закрытый пост — бот скачает его с использованием сохранённых cookies.
