# Приложение на своём сервере

Нужен сервер с Docker и домен, A-запись которого указывает на этот сервер. Порты 80 и 443 должны быть открыты.
Если дневник в своей базе, приложение ставится на тот же сервер, где работает Hermes: оно читает тот же файл базы.

## Шаги

1. Скопируйте репозиторий на сервер:
   ```bash
   git clone https://github.com/victorovi4/hermes-nutribot.git && cd hermes-nutribot/deploy
   ```
2. Заполните настройки:
   ```bash
   cp app.env.example app.env && nano app.env
   ```
   - `NUTRI_DOMAIN` — ваш домен;
   - `TELEGRAM_BOT_TOKEN` — токен бота (тот же, что у Hermes);
   - `TELEGRAM_ALLOWED_USERS` — ваш Telegram-id (узнать у `@userinfobot`);
   - для своей базы: `NUTRI_STORAGE=sqlite`, `NUTRI_DB_HOST_DIR` — папка профиля Hermes, `NUTRI_UID`/`NUTRI_GID` — вывод `id -u` и `id -g`;
   - для Google-таблицы: `NUTRI_STORAGE=sheets`, `NUTRI_SPREADSHEET_ID` и `GOOGLE_SA_KEY_B64` (ключ служебного аккаунта в base64).
3. Запустите:
   ```bash
   docker compose up -d --build
   ```
   Caddy сам получит сертификат. Проверьте: `curl -I https://<ваш домен>/` — должно быть `200`.
4. Добавьте кнопку в меню бота:
   ```bash
   NUTRI_APP_URL=https://<ваш домен>/ python3 set_menu_button.py
   ```

## Проверки и обслуживание

- `docker compose logs -f app` — что происходит; в журнале только метод и путь, без содержимого.
- `docker compose pull && docker compose up -d --build` — обновление после `git pull`.
- Резервная копия дневника: `hermes --profile <профиль> nutribot export` или копия файла `nutribot.db`.
- Приложение отвечает только вам: запрос без подписи Telegram получает 401, с чужим id — 403.
