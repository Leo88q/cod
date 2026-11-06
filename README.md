# 🎬 Сцена

Как работает услуга end-to-end и полный инвентарь фичей — `docs/07-service-flow.md`. Таймкоды — всегда **отрезки** (начало–конец, 25–75 сек), фиксируются снапшотом в открытке; источники и пайплайн — `docs/06-timecode-sources.md`. Аудит «что ещё не продакшн» и что закрыто — `docs/08-prod-gaps.md` (аналитика `GET /api/admin/analytics`, экспорт данных `GET /api/users/{tg}/export`, напоминание `gift_soon`, очистка медиа, онбординг бота, ступенчатый rate limit) (ЮKassa-webhook auth, лимит загрузок, CORS, fail-safe анонима, жалобы/удаление подарков, /privacy + /terms + чекбокс 152-ФЗ, WAL + `scripts/backup.sh`, CI, docker-compose).

Приложение «общий фильм» для двоих и семьи: даришь повод (букет) + **сцену из фильма с таймкодом** и личным сообщением — получатель отвечает своей сценой, и из сцен складывается ваш общий таймлайн.

Монетизация: QR-карточки в букеты для цветочных студий (B2B), комиссия 10–15% с заказов букетов через прямые ссылки магазинов на Flowwow (zero-commission для магазина), премиум «ваш фильм».

## Структура

- `docs/01-prd-scena.md` — PRD: идея, MVP, экономика, юридика, риски, KPI
- `docs/02-flowwow-playbook.md` — плейбук по Flowwow: комиссии, zero-commission ссылки, пилот со студиями, чеклист
- `docs/03-runbook.md` — запуск, env-переменные, настройка бота, продакшен-чеклист
- `docs/04-product-audit.md` — полный продуктовый аудит: сценарии, «будет ли интересно», метрики, gap-list
- `docs/05-flowwow-agency-design.md` — детальный дизайн агентского договора с Flowwow: сценарии A/B/C, атрибуция по ref, договор, вопросы к Flowwow, экономика
- `db/schema.sql` — схема БД (каталог сцен с полями лицензии/версии/проверки таймкода, подарки, ответы, студии, карточки, заказы, уведомления, согласия 152-ФЗ)
- `db/seed_scenes.sql`, `db/seed_bouquets.sql` — сиды (таймкоды — заглушки под ручную проверку)
- `app/main.py` — FastAPI: REST API + раздача Mini App (`/app/`) + медиа (`/media/`)
- `app/bot.py` — Telegram-бот (aiogram): deeplink `?startapp=g_<код>|card_<код>`, меню, шаринг
- `app/scheduler.py` — отложенные открытия, «сгорание» подарков, напоминания о датах, рассылка уведомлений
- `app/payments.py` — оплаты (ЮKassa в проде, dev-подтверждение локально): пачки карточек, подписки студий, премиум «ваш фильм»; идемпотентные эффекты
- `app/security.py` — валидация Telegram initData (HMAC), админ-токен
- `tests/` — автотесты API (pytest): `.venv/bin/python -m pytest tests/ -q` (118 тестов) + `tests/ui/` (Node/jsdom): `npm test` (14 тестов)
- `Dockerfile` — прод-образ: `docker build -t scena . && docker run -p 8000:8000 -e BOT_TOKEN=... scena`
- `app/static/landing.html` — лендинг продукта на `/`
- `scripts/import_scenes.py` / `scripts/export_scenes.py` — импорт/экспорт каталога сцен в CSV (`db/scenes_template.csv` — шаблон)
- Сверка таймкодов: админка `#/admin` → «⏱ Сверка таймкодов» или `POST /api/admin/scenes/{id}/verify`
- Премиум-экспорт «вашего фильма» в HTML: `GET /api/film/{tg}/export` (гейт 402 без премиума 499 ₽/год)
- `app/flowwow.py` — ссылки плееров, партнёрские ссылки заказов (ref/utm)
- `app/qr.py` — QR-коды (SVG)
- `app/static/` — Mini App: создание подарка, открытка, ответ, таймлайн, каталог, даты, «ваш фильм», букеты, кабинет студии

## Запуск (dev)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # заполни BOT_TOKEN, PUBLIC_URL, ADMIN_TOKEN
./scripts/dev.sh            # API + Mini App на http://localhost:8000/app/
BOT_TOKEN=... .venv/bin/python -m app.bot   # бот отдельным процессом
```

Без `BOT_TOKEN` API работает в анонимном dev-режиме (для превью вне Telegram).

## Основные API

`POST /api/scenes/search` · `POST /api/scenes/suggest` · `POST /api/gifts` · `GET /api/gifts/{id}` ·
`POST /api/gifts/{id}/reply` · `POST /api/gifts/{id}/notes` · `GET /api/gifts/{id}/qr.svg` ·
`POST /api/gifts/{id}/order-bouquet` · `GET /api/orders/{id}/go` · `GET /api/timeline/{tg}` ·
`GET /api/film/{tg}` · `GET/POST /api/dates` · `GET /api/bouquets` · `POST /api/shops` ·
`POST /api/shops/{id}/cards` · `GET /api/cards/{code}` · `GET /api/cards/{code}/qr.svg` ·
`GET /api/shops/{id}/stats` · `POST /api/upload` · админка: `/api/admin/*` (X-Admin-Token)

Новые (продакт-уровень, 2026-11):
`POST /api/scenes/semantic` (поиск по смыслу: «извиниться», «первая любовь», TF-IDF локально) ·
`GET /api/scenes/daily` («сцена дня» на лендинге) · `GET /api/lists` + `GET /api/lists/{id}` (10 подборок: 14 февраля, 8 марта, извинения…) ·
`GET /api/dates/{tg}/ics` (экспорт дат в календарь) · `GET /api/cards/{code}/preview` (печатный макет QR-карточки A6) ·
`GET /api/users/me` (премиум-статус) · подарок: поле `theme` (cinema/retro/neon + премиум paper), голосовое сообщение (запись в форме), уведомление дарителю при открытии (`notifications`, kind=gift_opened), конфетти при первом просмотре.

Библиотека таймкодов, автономная (подробнее — `docs/06-timecode-sources.md`): `scripts/fetch_timecodes.py` извлекает кандидатов из субтитров OpenSubtitles/OPUS семантически (RU: TF-IDF по смыслу нашего описания; EN: keyword-группы) + **консенсус** нескольких версий субтитров (±10 сек) → `timecode_candidates` с `confidence/consensus/auto` (только факт таймкода + sha1 + source/license, без текста реплик). **Авто-одобрение отключено по умолчанию**: `verified=1` ставится только вручную через `POST /api/admin/timecodes/{id}/review` с обязательным `version_note`; `POST /api/admin/timecodes/bulk-approve` доступен лишь при явном `ALLOW_BULK_AUTO_APPROVE=1`. Очередь: `GET /api/admin/timecodes`, метка момента — нашими словами. Клиент выбирает момент: `GET /api/scenes/{id}/moments` + чипы ⏱ в форме создания (`moments` в `POST /api/gifts`); таймкод **фиксируется снапшотом** в `gift_scenes` — открытка неизменна навсегда, `open_at` тоже задаётся один раз.

Каталог сцен как «система находок» (`#/catalog`): `GET /api/scenes/meta` отдаёт категории с подсчётами (поводы, настроения, жанры, эпохи, возраст, всего/проверено); фильтры — повод, настроение, жанр, эпоха, возраст, «✓ только проверенные», «⏱ с таймкодом», «⭐ закладки», сортировка (популярность/год/название), чипы подборок; текстовый запрос — семантический `POST /api/scenes/semantic`; состояние фильтров — в hash URL (шарable ссылка). `POST /api/scenes/search` принимает `genre/era/age/verified_only/has_timecode/sort`.

## Ручная сверка таймкодов и библиотека источников

Большой каталог (146 сцен) исторически был без реальных таймкодов: сиды содержали заглушки `"0:00:00"`, а SRT-образцы не коммитились. Теперь есть повторяемый мост к внешним библиотекам:

```bash
.venv/bin/python scripts/import_timecode_library.py   # полный манифест: 62 сцены / 105 SRT+CSV / 4 зеркала
.venv/bin/python scripts/import_timecode_library.py --pilot   # 20 кураторских сцен / 38 SRT
bash scripts/dev.sh                                    # затем #/admin с ADMIN_TOKEN
```

Импортёр сначала берёт файлы с `raw.githubusercontent.com` — это **не расходует лимит GitHub REST API**; если raw-хост закрыт, уходит в `api.github.com`. При повторном `HTTP 403 rate limit exceeded` задай `GITHUB_TOKEN`/`GH_TOKEN` в терминале (например `export GITHUB_TOKEN=$(gh auth token)`), токен в чат не передавай.

`source_registry.json` + `srt_index.json` описывают 4 GitHub-зеркала (39 861 + 1 300 + 14 SRT и 1 305 CSV), `imdb_map_full.json` даёт 133 IMDb ID на 140 сцен каталога. Для OPUS / OpenSubtitles v2024 на локальной машине есть `scripts/import_opus_timecodes.py`. Сводка покрытия — `GET /api/admin/timecode-library` и панель «📚 Библиотека таймкодов» в админке.

Инструкция оператора: [docs/13-timecode-verification.md](docs/13-timecode-verification.md), масштабирование/источники: [docs/14-timecode-library-scale.md](docs/14-timecode-library-scale.md), легальные deep links: [docs/15-moderation-links.md](docs/15-moderation-links.md), **служебная очередь на все 146 сцен** (свёрнута в админке): [docs/16-moderation-board.md](docs/16-moderation-board.md), **систематизация по 14 категориям**: [docs/17-intent-taxonomy.md](docs/17-intent-taxonomy.md), **как куратору отбирать**: [docs/18-curator-workflow.md](docs/18-curator-workflow.md), **первый блок «признание в любви и чувства» на 50 фильмов**: [docs/20-love-confession-batch.md](docs/20-love-confession-batch.md). Админка показывает одну компактную таблицу на 50 строк: фильм, редактируемая прямая ссылка VK/Rutube, начало, конец, версия издания и проверка. У 9 фильмов ссылка на точную официальную полную версию уже подставлена, у остальных 41 поле пустое — поисковые URL и чужие «Мосфильм»-ссылки не подставляются. Таймкоды подготовлены, но не проверены: «💾» сохраняет правки без `verified`, «✓» подтверждает только с `version_note` после просмотра; пиратские зеркала не используются.

## Юридические рамки

Показываем только название фильма, год, таймкод и наше описание сцены — без видео и дословных реплик. Персональные данные — по 152-ФЗ (согласия в таблице `consents`, уведомление Роскомнадзора — в чеклисте runbook).

## Актуальный чек-лист реализации

[Функции, код, дизайн и очередь до пилота — аудит 04.11.2026, актуализация 06.11.2026](docs/09-implementation-checklist.md). В шапке документа — таблица factual-проверок (`py_compile`, 118 pytest, 14 UI, `CATALOG_CLEAN`, `SMOKE OK`, restore drill).
Статусы разделяют реализованный код, частичную готовность и неподтверждённый продакшен; актуальнее ранних оценок в `docs/08-prod-gaps.md`.

Пакет подготовки к production (worker, TLS ingress, preflight, диагностика, проверка backup и внешняя приёмка): [docs/10-deploy-readiness.md](docs/10-deploy-readiness.md).

Следующий пакет реализации (взаимное согласие связей, владельцы студий, удаление профиля, нормализация файлов): [docs/11-product-completion.md](docs/11-product-completion.md).

Эксплуатационная диагностика: [docs/12-operations.md](docs/12-operations.md) — audit, heartbeat worker, dead letters/retry и read-only мониторинг.

**Источники просмотра (обновление):** основная таблица модерации использует только точные прямые ссылки VK Видео / Rutube (9 предзаполнены, остальные поля пустые и редактируемые); служебные панели и пользовательские «где смотреть» тоже ведут только в VK Видео и Rutube. YouTube/Кинопоиск/ivi/Okko/IMDb-ссылки и поисковые URL в основной таблице удалены. Подробнее — `docs/15-moderation-links.md`.
