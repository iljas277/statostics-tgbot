# Telegram Channel Admin Bot

Python-бот и FastAPI-панель для аналитики Telegram-канала и discussion-группы.

## Возможности

- Сбор комментариев из discussion group и сохранение в SQLite.
- Аналитика по постам и пользователям.
- Экспорт CSV:
  - уникальные комментаторы,
  - комментаторы конкретного поста,
  - агрегированные реакции поста,
  - полный user-metrics отчет.
- Web API + Dashboard + PNG-графики.
- MTProto-метрики по постам (views/reactions/forwards) через Telethon.

## Команды бота

- `/start` - стартовая справка.
- `/help` - список команд.
- `/stats [hours]` - сводка за период.
- `/poststats <message_id>` - аналитика конкретного поста.
- `/chart [days]` - график комментариев.
- `/refreshmetrics [posts_limit]` - обновление MTProto-снапшотов.
- `/contacts` - кешированный список активных комментаторов.
- `/refreshcontacts` - пересборка кеша контактов.
- `/export_user_metrics_csv [limit]` - CSV по пользователям и их метрикам.
- `/export_commenters_csv [limit]` - CSV уникальных комментаторов канала.
- `/export_post_commenters_csv <message_id> [limit]` - CSV комментаторов поста.
- `/export_post_reactions_csv <message_id>` - CSV агрегированных реакций поста.
- `/binddiscussion` - привязка discussion-чата в runtime.

Удалены как bot-команды:

- `/post`, `/edit`, `/delete`
- `/tgstats`
- `/health`

Эти операции оставлены в HTTP API.

## FastAPI API

Базовый URL: `http://WEB_HOST:WEB_PORT`

Swagger:

- `/docs`
- `/redoc`
- `/openapi.json`

### JSON endpoints

- `GET /api/summary`
- `GET /api/stats?hours=24`
- `GET /api/tgstats?posts_limit=50`
- `GET /api/top-posts?limit=20`
- `GET /api/poststats/{message_id}`
- `GET /api/reactions/post/{message_id}`
- `GET /api/contacts`
- `GET /api/commenters/channel?limit=3000`
- `GET /api/commenters/post/{message_id}?limit=3000`
- `GET /api/user-metrics?limit=5000`

### Post management endpoints (вместо bot-команд)

- `POST /api/posts`
  - body: `{ "text": "..." }`
- `PATCH /api/posts/{message_id}`
  - body: `{ "text": "..." }`
- `DELETE /api/posts/{message_id}`

### CSV endpoints

- `GET /api/export/commenters/channel.csv?limit=3000`
- `GET /api/export/commenters/post/{message_id}.csv?limit=3000`
- `GET /api/export/reactions/post/{message_id}.csv`
- `GET /api/export/user-metrics.csv?limit=5000`

### Chart endpoints

- `GET /chart/comments.png?days=14`
- `GET /chart/views.png?days=14`
- `GET /chart/reactions.png?days=14`
- `GET /chart/forwards.png?days=14`

## User-metrics CSV

`/export_user_metrics_csv` и `/api/export/user-metrics.csv` включают:

- `user_id`
- `nickname`
- `username`
- `first_name`
- `last_name`
- `profile_url`
- `comments_count`
- `posts_commented_count`
- `linked_chats_count`
- `first_comment_at`
- `last_comment_at`
- `last_activity`
- `comments_with_contact`
- `comments_with_intent`
- `contacts_rank`

## Конфигурация (.env)

Обязательные:

- `BOT_TOKEN`
- `CHANNEL_ID`
- `ADMIN_IDS`

Для комментариев:

- `LINKED_CHAT_ID` (можно пустым, если делаете `/binddiscussion`)

Для MTProto:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_API_SESSION` (по умолчанию `data/telethon.session`)
- `TELEGRAM_PROXY_URL` (опционально, `socks5://...`)

Web:

- `WEB_HOST` (по умолчанию `127.0.0.1`)
- `WEB_PORT` (по умолчанию `8080`)
- `WEB_RELOAD` (`true/false`)

Кеш и расписание:

- `CONTACTS_POSTS_LIMIT` (по умолчанию `14`)
- `CONTACTS_COMMENTERS_LIMIT` (по умолчанию `10`)
- `CONTACTS_REFRESH_HOUR`
- `CONTACTS_REFRESH_MINUTE`
- `TZ`

Прочее:

- `MTPROTO_METRICS_POSTS_LIMIT`
- `CHART_DEFAULT_DAYS`
- `RUN_STARTUP_TESTS`

## Быстрый старт

```bash
cd /path/to/tg_channel_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Запуск бота:

```bash
python3 main.py
```

Запуск веб-панели:

```bash
python3 run_web.py
```

## Миграции БД

При `db.init_schema()` автоматически применяются миграции:

- удаление устаревшей таблицы `leads`;
- миграция `stats_snapshots` без столбца `leads_count`.

## Тестирование

Тесты переведены на `pytest`.

Запуск:

```bash
python3 -m pytest -q
```

Покрываются:

- репозиторий и агрегации,
- миграция БД,
- chart helper-функции,
- web JSON/CSV endpoints,
- API CRUD постов (через mock Bot).
