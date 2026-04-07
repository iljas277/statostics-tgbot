# Telegram Channel Admin Bot (MVP)

Python-бот для администрирования Telegram-канала:
- публикация, редактирование, удаление постов;
- сбор комментариев из discussion group;
- поиск активных комментаторов как потенциальных лидов;
- статистика по постам/комментариям и снапшоты в SQLite.

## Что входит в MVP

- Команды:
  - `/post <text>`
  - `/edit <message_id> <new_text>`
  - `/delete <message_id>`
  - `/stats [hours]`
   - `/chart [days]`
   - `/tgstats [posts_limit]`
   - `/refreshmetrics [posts_limit]`
    - `/poststats <message_id>`
   - `/contacts [limit]`
   - `/refreshcontacts`
   - `/export_commenters_csv [limit]`
   - `/export_post_commenters_csv <message_id> [limit]`
   - `/export_post_reactors_csv <message_id> [limit]`
    - `/binddiscussion`
    - `/health`
- База SQLite с таблицами постов, комментариев, лидов и логов действий.
- Периодическая задача: снапшот статистики каждые 6 часов.

## Как работает каждая ручка

- `/start`
   - Показывает список доступных команд и короткую справку.

- `/post <text>`
   - Публикует новый пост в канале `CHANNEL_ID`.
   - Сохраняет `message_id`, текст и время в таблицу `posts`.
   - Логирует действие администратора в `bot_actions`.

- `/edit <message_id> <new_text>`
   - Редактирует текст существующего поста в канале.
   - Обновляет запись в `posts` и фиксирует время изменения.
   - Логирует действие в `bot_actions`.

- `/delete <message_id>`
   - Удаляет пост из канала.
   - Ставит отметку удаления в БД (`deleted_at`).
   - Логирует действие в `bot_actions`.

- `/stats [hours]`
   - Возвращает агрегированную статистику за период (по умолчанию 24 часа):
      - количество постов;
      - количество комментариев;
      - число уникальных комментаторов;
      - количество лидов.

- `/poststats <message_id>`
   - Возвращает аналитику по конкретному посту:
      - создан/обновлен/удален;
      - общее число комментариев;
      - число уникальных комментаторов;
      - сколько лидов пришло из комментариев этого поста;
      - время последнего комментария;
      - топ-5 комментаторов по этому посту.
      - (если настроен MTProto) views, reactions, forwards и разбивка реакций.

- `/chart [days]`
   - Отправляет PNG-график тренда комментариев за выбранный период.
   - Если `days` не указан, используется `CHART_DEFAULT_DAYS`.

- `/tgstats [posts_limit]`
   - Возвращает агрегаты Telegram API по последним постам:
      - суммарные просмотры;
      - средние просмотры на пост;
      - суммарные реакции;
      - суммарные пересылки.

- `/refreshmetrics [posts_limit]`
   - Принудительно обновляет MTProto-метрики по последним постам канала.

- `/contacts [limit]`
   - Показывает только ник и ссылку на профиль.
   - Список строится по последним `CONTACTS_POSTS_LIMIT` постам.
   - В список попадают только топ `CONTACTS_COMMENTERS_LIMIT` комментаторов.
   - Берет данные из кеш-таблицы `contacts_cache`.

- `/refreshcontacts`
    - Принудительно пересобирает кеш списка контактов прямо сейчас.

- `/export_commenters_csv [limit]`
   - Выгружает CSV всех уникальных комментаторов канала.
   - Формат: `nickname,comments_count`.
   - Сортировка: по количеству комментариев (убывание).

- `/export_post_commenters_csv <message_id> [limit]`
   - Выгружает CSV всех комментаторов указанного поста.
   - Формат: `nickname,comments_count`.
   - Сортировка: по количеству комментариев (убывание).

- `/export_post_reactors_csv <message_id> [limit]`
   - Выгружает CSV пользователей, оставивших реакции на указанный пост (MTProto).
   - Формат: `nickname,reactions_count,positive_count,negative_count`.
   - `positive/negative` считаются эвристически по набору emoji.

- `/binddiscussion`
   - Привязывает текущую группу как discussion-chat в runtime.
   - Используйте команду внутри discussion-группы.

- `/health`
   - Диагностика ingestion:
      - проверка linked chat и доступа к нему;
      - privacy mode;
      - статус бота в linked chat;
      - сколько сообщений и автофорвардов увидел бот;
      - состояние таблиц `discussion_map/comments/users`.

## Ограничения Bot API и MTProto

В каналах Bot API не дает надежно собирать `views` и агрегаты реакций. Поэтому для этих метрик используется MTProto (Telethon) с отдельными credentials.

## Быстрый старт

1. Создайте и активируйте окружение:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
3. Подготовьте конфиг:
   ```bash
   cp .env.example .env
   ```
4. Заполните `.env`:
   - `BOT_TOKEN`
   - `CHANNEL_ID`
   - `LINKED_CHAT_ID` (для комментариев)
   - `ADMIN_IDS`
   - `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` (для views/реакций)
5. Запустите бота:
   ```bash
   python main.py
   ```

## Требования к Telegram

- Бот добавлен в канал как администратор (права на публикацию/редактирование/удаление).
- Если нужна аналитика комментариев: канал должен быть связан с discussion group, и бот должен быть в ней.
- В BotFather желательно отключить privacy mode (`/setprivacy -> Disable`), иначе бот в группе может видеть только команды.

## SSH port forwarding (операционный этап до Telegram API/Telethon)

Пока интеграции с Telegram API и Telethon не реализованы, сетевой маршрут можно подготовить через SSH-туннель:

1. Поднимите SOCKS5-туннель:
   ```bash
   ssh -N -D 127.0.0.1:1080 user@remote-server
   ```
2. Направляйте внешние запросы через `socks5://127.0.0.1:1080`.
3. Для продакшена установите `autossh` и используйте `systemd`, чтобы туннель автоматически восстанавливался.

Пошаговый runbook: `infra/ssh-portforwarding.md`, шаблон юнита: `infra/systemd/bot-socks-tunnel.service`.

## Предстартовые тесты

Перед запуском бота автоматически выполняются:
- локальные unit-тесты (`python -m unittest discover -s tests -p "test_*.py"`);
- проверка доступа бота к Telegram и каналу;
- при включенном MTProto — пробный запрос метрик.

Отключение: `RUN_STARTUP_TESTS=false`.

## Если MTProto пишет про bot users restricted

Ошибка вида `BotMethodInvalidError: ... cannot be executed as a bot` означает, что файл `TELEGRAM_API_SESSION` авторизован как бот, а не как пользователь.

Сделайте переавторизацию Telethon-сессии:

1. Остановите бота.
2. Удалите старую сессию:
   - `rm -f data/telethon.session data/telethon.session-journal`
3. Запустите login-flow:
   - `python scripts/telethon_login_user.py`
4. Введите номер телефона, код из Telegram и при необходимости 2FA-пароль.
5. Запустите бота снова и выполните `/refreshmetrics`.

## Если комментарии не считаются

1. Запустите `/health` в личке с ботом и проверьте:
   - `Bot privacy disabled: True`
   - `Seen group messages` растет после сообщений в discussion-чате.
2. Вызовите `/binddiscussion` прямо в discussion-группе.
3. Напишите комментарий под постом и снова проверьте `/health` + `/stats 24`.

## Структура проекта

- `main.py` - entrypoint
- `app/config.py` - загрузка настроек
- `app/db.py` - схема и подключение SQLite
- `app/repositories.py` - операции БД
- `app/services.py` - анализ текста комментариев (lead scoring)
- `app/handlers.py` - команды и обработка сообщений
- `app/jobs.py` - периодические фоновые задачи
- `app/charts.py` - генерация PNG-графиков
- `app/web.py` - FastAPI веб-панель
- `run_web.py` - запуск веб-панели
- `web/templates/dashboard.html` - шаблон дашборда

## Настройки эффективности списка контактов

В `.env`:

- `CONTACTS_POSTS_LIMIT` - по скольким последним постам считать активность.
- `CONTACTS_COMMENTERS_LIMIT` - сколько комментаторов хранить в выдаче `/contacts`.
- `CONTACTS_REFRESH_HOUR` и `CONTACTS_REFRESH_MINUTE` - ежедневное время пересборки списка.
- `TZ` - часовой пояс для расписания.

## Веб-панель FastAPI

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
2. Настройте в `.env`:
   - `WEB_HOST` (например `127.0.0.1`)
   - `WEB_PORT` (например `8080`)
   - `CHART_DEFAULT_DAYS` (например `14`)
3. Запустите панель:
   ```bash
   python run_web.py
   ```
4. Откройте:
    - `http://WEB_HOST:WEB_PORT/` - дашборд
    - `http://WEB_HOST:WEB_PORT/api/summary` - JSON-сводка
    - `http://WEB_HOST:WEB_PORT/api/stats?hours=24` - JSON-статистика за период
    - `http://WEB_HOST:WEB_PORT/api/tgstats?posts_limit=50` - JSON-агрегаты MTProto
    - `http://WEB_HOST:WEB_PORT/api/poststats/<message_id>` - JSON аналитика поста
    - `http://WEB_HOST:WEB_PORT/api/contacts` - JSON контакты из кеша
    - `http://WEB_HOST:WEB_PORT/api/commenters/channel` - JSON уникальные комментаторы канала
    - `http://WEB_HOST:WEB_PORT/api/commenters/post/<message_id>` - JSON комментаторы поста
    - `http://WEB_HOST:WEB_PORT/api/reactors/post/<message_id>` - JSON реакторы поста (MTProto)
    - `http://WEB_HOST:WEB_PORT/api/export/commenters/channel.csv` - CSV уникальные комментаторы канала
    - `http://WEB_HOST:WEB_PORT/api/export/commenters/post/<message_id>.csv` - CSV комментаторы поста
    - `http://WEB_HOST:WEB_PORT/api/export/reactors/post/<message_id>.csv` - CSV реакторы поста (MTProto)
    - `http://WEB_HOST:WEB_PORT/chart/comments.png?days=14` - график
    - `http://WEB_HOST:WEB_PORT/chart/views.png?days=14` - график просмотров
    - `http://WEB_HOST:WEB_PORT/chart/reactions.png?days=14` - график реакций
    - `http://WEB_HOST:WEB_PORT/chart/forwards.png?days=14` - график репостов

## Дальше (Phase 2)

- Экспорт лидов в CSV.
- Расширение MTProto-метрик (ERR, retention, cohort по периодам).
- Веб-панель (FastAPI) для операторов.
