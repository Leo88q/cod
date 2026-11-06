# Runbook «Сцена» — запуск и эксплуатация

## Локальный запуск

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # BOT_TOKEN, PUBLIC_URL, ADMIN_TOKEN
./scripts/dev.sh       # API + Mini App на http://localhost:8000/app/
BOT_TOKEN=... .venv/bin/python -m app.bot   # бот отдельно
```

Без `BOT_TOKEN` API работает в анонимном dev-режиме (`ALLOW_ANON=1`) — удобно для превью.

## Переменные окружения (.env)

| Переменная | Зачем |
|---|---|
| `BOT_TOKEN` | токен от @BotFather; включает валидацию initData и рассылку уведомлений |
| `BOT_USERNAME` | username бота для deeplink-ов `t.me/<bot>?startapp=g_<код>` |
| `PUBLIC_URL` | внешний URL (превью/прод); по умолчанию берётся из запроса |
| `ADMIN_TOKEN` | токен админки, заголовок `X-Admin-Token` |
| `SHOP_COMMISSION_PCT` | наша комиссия с магазинов, % (default 10) |
| `GIFT_TTL_DAYS` | сколько живёт неоткрытый подарок (default 30) |
| `DB_PATH` / `MEDIA_DIR` | пути к БД и медиа |

## Настройка бота в @BotFather

1. `/newbot` → получаешь токен → в `.env`.
2. `/setmenubutton` или в BotFather: **Menu Button → Configure → Web App** — URL: `https://<твой-домен>/app/`.
3. В BotFather: **Bot Settings → Allow users to... / Group Privacy** — выключить, если бот в группах.
4. Deeplink подарка: `https://t.me/<bot>?startapp=g_<код>`; карточки: `...?startapp=card_<код>`.

## Потоки данных

- **Создание подарка**: Mini App → `POST /api/gifts` → код → deeplink/QR (`/api/gifts/{id}/qr.svg`).
- **Открытие**: получатель → `/app/#/g/<код>` → `GET /api/gifts/{id}` (учёт просмотров, отложенное открытие, серия по дням).
- **Ответ**: `POST /api/gifts/{id}/reply` → очередь уведомлений → шедулер (каждые 30 с) отправляет дарителю через бота.
- **Букет**: `POST /api/gifts/{id}/order-bouquet` → заказ в `orders` + ссылка агрегатора с `ref`/`utm` → `GET /api/orders/{id}/go` фиксирует клик и редиректит.
- **Карточка студии**: `POST /api/shops/{id}/cards` → коды → `/app/#/card/<код>` → QR (`/api/cards/{code}/qr.svg`).
- **Напоминания о датах**: шедулер раз в 30 с проверяет `important_dates` и шлёт уведомления за N дней.

## Админка (заголовок `X-Admin-Token`)

- `GET /api/admin/overview` — общие счётчики и выручка
- `GET /api/admin/scenes?status=draft` → `POST /api/admin/scenes/{id}/status` — модерация каталога
- `POST /api/admin/shops/{id}/subscribe?days=30` — активация подписки студии

## Оплаты (ЮKassa / dev)

Виды платежей (`payments` таблица): `card_pack` (пачка QR-карточек, цена за штуку, мин. заказ), `subscription` (месяц кабинета студии), `premium_film` (премиум «ваш фильм» на год).

- **Dev**: без `YUKASSA_SHOP_ID/SECRET_KEY` платёж создаётся с `provider=dev`, оплата подтверждается кнопкой на `#/pay/<id>` → `POST /api/payments/{id}/confirm-dev` (идемпотентно).
- **Prod**: с ключами ЮKassa создаётся реальный платёж (redirect), webhook `POST /api/payments/webhook` (событие `payment.succeeded`, idempotency по `metadata.payment_id`).
- Покупка карточек студией: `POST /api/shops/{id}/buy-cards {count}` → платёж → после оплаты карточки зачисляются на баланс (`cards` + `shops.card_balance`).
- Цены в env: `PRICE_PER_CARD`, `PRICE_CARD_PACK_MIN`, `PRICE_SUBSCRIPTION_MONTH`, `PRICE_PREMIUM_FILM`.

## Webhook-режим бота (prod)

Вместо polling: `WEBHOOK_URL=https://<домен>` + `WEBHOOK_SECRET` → API-процесс поднимает `POST /tg/webhook` (заголовок `X-Telegram-Bot-Api-Secret-Token`), бот обрабатывает обновления через общий dispatcher. В dev — `python -m app.bot` (polling).

## Работа с каталогом сцен (главный актив)

- Наполнение: ручной сид (`db/seed_scenes*.sql`) + CSV-импорт `scripts/import_scenes.py file.csv` (status=draft) + пользовательские предложения (`POST /api/scenes/suggest`).
- Модерация: админка в Mini App `#/admin` (токен `ADMIN_TOKEN`) или API: `GET /api/admin/scenes?status=draft`, `POST /api/admin/scenes/{id}/status`.
- **Сверка таймкодов**: `GET /api/admin/scenes?status=approved&verified=0` → открыть сцену в легальном плеере → `POST /api/admin/scenes/{id}/verify {service, timecode_label, timecode_seconds, version_note, watch_url}`. В UI — та же форма в админке. Без `verified=1` сцена помечается как «таймкод требует проверки».
- Выгрузка каталога: `scripts/export_scenes.py out.csv --status approved`.
- Юрило: в описаниях только наш пересказ — без дословных реплик; у каждой записи `source` + `source_license`.
- **Большой каталог (140+ сцен)**: `scripts/catalog_data_a.py` (RU/СССР) + `scripts/catalog_data_b.py` (мир) → `scripts/gen_catalog.py` → `db/seed_catalog.sql` (идемпотентно: `INSERT OR IGNORE` + уникальный индекс `idx_scenes_title_year`). Сид выполняется при каждой инициализации БД (`init_db` в `app/db.py`), плюс 10 кураторских подборок в `scene_lists`. Таймкоды в каталоге NULL и `verified=0` — вносить вручную через админку после проверки в легальном плеере (не выдумывать!).
- **Семантический поиск**: `POST /api/scenes/semantic {query}` — локальный TF-IDF (scikit-learn, RU стоп-слова) по названию+жанру+тегам+описаниям, косинус; индекс кэшируется. В каталоге Mini App поиск по тексту идёт через него.
- **Каталог-фильтры** (`#/catalog`): `GET /api/scenes/meta` — категории с подсчётами (поводы/настроения/жанры/эпохи/возраст + total/verified). `POST /api/scenes/search` поддерживает `occasion, mood, query, genre, era, age, verified_only, has_timecode, sort (popular|year_desc|year_asc|title)`. В UI: чипы поводов и настроений, селекты жанра/эпохи/возраста, чекбоксы «✓ проверенные», «⏱ с таймкодом», «⭐ закладки», чипы подборок, сортировка, счётчик «Найдено N», состояние — в hash URL.
- **Подборки**: `GET /api/lists`, `GET /api/lists/{id}` — чипы над результатами каталога.
- **«Сцена дня»** на лендинге: `GET /api/scenes/daily`.
- **Темы открытки**: поле `gifts.theme` (миграция в `app/db.py`), 4 темы в форме создания; `paper` — только с премиумом (`GET /api/users/me` → `premium`). Класс `theme-*` на экране подарка.
- **Голосовое сообщение**: запись через MediaRecorder в форме → `POST /api/upload` → `message_voice` подарка; аудио в просмотре подарка.
- **Уведомление при открытии**: при переводе подарка created→opened в `notifications` ставится `gift_opened` дарителю (бот отправит, если BOT_TOKEN настроен).
- **Даты → календарь**: `GET /api/dates/{tg}/ics` (text/calendar, RRULE:FREQ=YEARLY) — кнопка на экране «Важные даты».
- **Печатная карточка**: `GET /api/cards/{code}/preview` — HTML-макет A6 со встроенным QR; ссылка в кабинете студии после выпуска карточек.
- **Таймкод = отрезок (начало–конец)**: у сцен/кандидатов/снапшота подарка есть `timecode_end_label/seconds` (из end блока субтитров, доклеенного до клипа 25–75 сек). Везде в UI показывается диапазон `0:50:19–0:50:56`. Полный флоу услуги — `docs/07-service-flow.md`.
- **Библиотека таймкодов** (автономная, см. `docs/06-timecode-sources.md`): `scripts/fetch_timecodes.py --manifest data/subtitles_samples/manifest.json` парсит .srt (OpenSubtitles/OPUS, GitHub-образцы; несколько файлов одного фильма = консенсус ±10 сек). Семантический матчинг по смыслу для RU-субтитров (TF-IDF + стемминг), keyword-группы для EN. Кандидаты в `timecode_candidates` (только время + sha1 + source/license + confidence/consensus/auto, без текста). Авто-одобрение консенсуса **отключено по умолчанию**: `POST /api/admin/timecodes/bulk-approve` вернёт 403, пока явно не задан `ALLOW_BULK_AUTO_APPROVE=1`; кнопка ⚡ в админке заблокирована. Ручная очередь: `GET /api/admin/timecodes?status=pending`, `POST /api/admin/timecodes/{id}/review {action, version_note, moment_label}` — `version_note` обязателен, метка момента нашими словами, без реплик. Моменты сцены клиенту: `GET /api/scenes/{id}/moments`; выбор момента — `moments` в `POST /api/gifts`, снапшот таймкода фиксируется в `gift_scenes` (открытка неизменна). Образцы SRT — в `data/subtitles_samples/` (в git не коммитятся), скачивание — через `raw.githubusercontent.com` с fallback на `api.github.com`.
- **Импорт из большой библиотеки**: `scripts/import_timecode_library.py` по умолчанию читает `full_manifest.json` (62 сцены / 105 SRT+CSV из 4 GitHub-зеркал) и `imdb_map_full.json` (133 IMDb ID); `--pilot` переключает на 20 кураторских сцен. `scripts/build_srt_index.py` обновляет инвентарь 41 175 путей, `scripts/build_full_manifest.py` пересобирает манифест, `scripts/import_opus_timecodes.py` читает OPUS v2024 на Mac. Загрузка SRT идёт через `raw.githubusercontent.com` с fallback на `api.github.com` + `GITHUB_TOKEN` при 403. Сводка: `GET /api/admin/timecode-library`. Основной экран: `GET /api/admin/moderation` + `docs/16-moderation-board.md` (все 146 сцен, gate по категориям). Таксономия: `docs/17-intent-taxonomy.md`, сценарий куратора: `docs/18-curator-workflow.md`. Инструкция: `docs/13-timecode-verification.md`, масштаб: `docs/14-timecode-library-scale.md`, легальные deep links: `docs/15-moderation-links.md` (только VK Видео + Rutube). Авто-консенсус **не** заменяет ручную проверку: кнопка ⚡ заблокирована, `version_note` обязателен при «✓ Подтвердить».
- **Полная проверка**: `bash scripts/check.sh` (py_compile + pytest + node --check + регенерация каталога + smoke живого сервера).

## Премиум «ваш фильм»

`GET /api/film/{tg}/export` отдаёт отдельный HTML (обложка + статистика + таймлайн), который печатается в PDF/книгу. Доступ гейтится `users.premium_until` (402 без оплаты 499 ₽/год, покупка — `POST /api/payments/create {kind:"premium_film"}`).

## Продакшен-чеклист

- [ ] PostgreSQL вместо SQLite (диалект схемы совместим: TEXT-даты → timestamptz, JSON → jsonb)
- [ ] Webhook вместо polling для бота (`aiogram` webhook на FastAPI route) + HTTPS
- [ ] Выключить `ALLOW_ANON`, оставить только валидный initData
- [ ] Уведомление Роскомнадзора (оператор ПДн), политика на домене, согласия в `consents`
- [ ] Эквайринг (ЮKassa/Точка) для оплаты карточек и подписок студий
- [ ] Договоры с агрегаторами/студиями (агентский, поручение на обработку ПДн)
- [ ] Юрист по авторскому праву: только фильм/год/таймкод/описание, без реплик и видео
- [ ] Мониторинг: /health, логи шедулера, алерты на очередь notifications
