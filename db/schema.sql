-- Схема БД «Сцена» v2. Диалект: SQLite-совместимый (TEXT вместо timestamptz/jsonb),
-- чтобы один файл работал и в dev (SQLite), и как основа для PostgreSQL в проде.
-- В проде: TEXT-даты -> timestamptz, JSON-поля -> jsonb.

PRAGMA foreign_keys = ON;

-- ---------- пользователи ----------
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id   INTEGER UNIQUE,          -- NULL для веб-пользователей вне Telegram
  username      TEXT,
  first_name    TEXT,
  phone         TEXT,
  lang          TEXT,
  is_admin      INTEGER NOT NULL DEFAULT 0,
  premium_until TEXT,                    -- до какого момента оплачен премиум «ваш фильм»
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- каталог сцен (главный актив) ----------
CREATE TABLE IF NOT EXISTS scenes (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  movie_title      TEXT NOT NULL,
  movie_year       INTEGER,
  imdb_id          TEXT,                 -- tt0111161 и т.п. — точное сопоставление с библиотеками субтитров
  tmdb_id          TEXT,                 -- запасной идентификатор для постеров/метаданных
  service          TEXT,                 -- на каком сервисе проверен таймкод: kinopoisk / ivi / okko / youtube
  version_note     TEXT,                 -- версия издания, для которой верен таймкод (иначе он «плывёт»)
  timecode_seconds INTEGER,              -- позиция в секундах
  timecode_label   TEXT,                 -- "1:23:45" — что показываем пользователю
  watch_url        TEXT,                 -- прямой deep link в легальный плеер с таймкодом (если известен)
  poster_url       TEXT,                 -- обложка (хостинг с лицензией, напр. TMDB w/ attribution)
  genre            TEXT,
  era              TEXT,                 -- эпоха фильма: 60-е, 90-е, 2010-е...
  age_rating       TEXT,
  mood_tags        TEXT NOT NULL DEFAULT '[]',     -- JSON-массив: романтика, ностальгия, юмор...
  occasion_tags    TEXT NOT NULL DEFAULT '[]',     -- JSON-массив: признание, извинение, поддержка, др...
  description      TEXT NOT NULL,         -- наше описание сцены (БЕЗ дословных реплик из сценария)
  why_fits         TEXT,                 -- одна строка «почему эта сцена подходит под повод»
  source           TEXT,                 -- откуда запись: manual / user_suggestion / partner
  source_license   TEXT,                 -- лицензия источника данных (для аудита)
  primary_intent   TEXT,                 -- основной интент из intent_taxonomy.json
  fragment_type    TEXT,                 -- ожидаемый тип фрагмента: monologue / dialogue / song_music / ...
  intent_query     TEXT,                 -- поисковый запрос под эту пару (без текста субтитров)
  curated_anchor   TEXT,                 -- конкретный эпизод, который ищем, НЕ таймкод
  intent_confidence REAL DEFAULT 0,
  intent_source    TEXT,                 -- manual / rule / fallback
  status           TEXT NOT NULL DEFAULT 'draft',  -- draft / approved / rejected
  suggested_by     INTEGER REFERENCES users(id),
  verified         INTEGER NOT NULL DEFAULT 0,     -- таймкод проверен вручную в плеере
  verified_at      TEXT,
  popularity       INTEGER NOT NULL DEFAULT 0,     -- сколько раз сцену выбирали в подарки
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);
CREATE INDEX IF NOT EXISTS idx_scenes_occasion ON scenes(occasion_tags);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenes_title_year ON scenes(movie_title, movie_year);

-- ---------- подарки ----------
CREATE TABLE IF NOT EXISTS gifts (
  id           TEXT PRIMARY KEY,          -- короткий код для deeplink/QR (напр. "aX3kQ9")
  giver_id     INTEGER REFERENCES users(id),
  card_id      INTEGER REFERENCES cards(id),   -- если создан по QR-карточке студии
  shop_id      INTEGER REFERENCES shops(id),
  recipient_name TEXT NOT NULL,
  recipient_city TEXT,                    -- город получателя — для автоподбора букетов
  title        TEXT,                      -- заголовок открытки, напр. «С 8 марта, любимая»
  occasion     TEXT,                      -- др / годовщина / признание / извинение...
  tone         TEXT,                      -- тёплый / игривый / романтичный / лаконичный
  message_text TEXT,
  message_voice_file_id TEXT,            -- telegram file_id ИЛИ локальный путь /media/...
  message_photo_file_id TEXT,
  format       TEXT NOT NULL DEFAULT 'single',  -- single / series
  theme        TEXT NOT NULL DEFAULT 'cinema',  -- тема открытки: cinema / retro / neon / paper (premium)
  soon_notified INTEGER NOT NULL DEFAULT 0,     -- напоминание дарителю «до открытия 1 день» отправлено
  inactive_at TEXT,
  series_days  INTEGER NOT NULL DEFAULT 1,
  open_at      TEXT,                      -- отложенное открытие (ISO8601)
  expires_at   TEXT,                      -- когда подарок «сгорает» (по умолчанию +30 дней)
  status       TEXT NOT NULL DEFAULT 'created', -- created / opened / replied / expired
  view_count   INTEGER NOT NULL DEFAULT 0,
  flowwow_ref  TEXT,                      -- ref-метка заказа букета (если заказывали)
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  opened_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_gifts_giver ON gifts(giver_id);
CREATE INDEX IF NOT EXISTS idx_gifts_status ON gifts(status);

-- сцены, прикреплённые к подарку (для серии — несколько, по дням)
CREATE TABLE IF NOT EXISTS gift_scenes (
  gift_id    TEXT NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  scene_id   INTEGER NOT NULL REFERENCES scenes(id),
  day_index  INTEGER NOT NULL DEFAULT 0,   -- 0 для single; 0..N-1 для series
  -- снапшот таймкода на момент создания подарка: открытка фиксирована,
  -- даже если таймкод сцены в каталоге позже изменится
  timecode_label TEXT,
  timecode_seconds INTEGER,
  PRIMARY KEY (gift_id, day_index)
);

-- ---------- ответы получателей ----------
CREATE TABLE IF NOT EXISTS replies (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  gift_id       TEXT NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  respondent    TEXT,                    -- имя получателя (или telegram user)
  respondent_user_id INTEGER REFERENCES users(id),
  type          TEXT NOT NULL,           -- scene / reaction / message
  scene_id      INTEGER REFERENCES scenes(id),
  reaction      TEXT,                    -- эмодзи-реакция
  message_text  TEXT,
  voice_file_id TEXT,
  photo_file_id TEXT,
  media_path    TEXT,                    -- загруженное медиа (фото/голосовое) нашим аплоадом
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_replies_gift ON replies(gift_id);

-- ---------- связи (пары) ----------
CREATE TABLE IF NOT EXISTS connections (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_a     INTEGER NOT NULL REFERENCES users(id),
  user_b     INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending',
  requested_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_a, user_b)
);

-- заметки и фото к моментам таймлайна
CREATE TABLE IF NOT EXISTS gift_notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  gift_id    TEXT NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  author_id  INTEGER REFERENCES users(id),
  text       TEXT,
  photo_file_id TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- цветочные студии (B2B) ----------
CREATE TABLE IF NOT EXISTS shops (
  owner_user_id INTEGER REFERENCES users(id),
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  city          TEXT,
  contact       TEXT,                    -- телефон/telegram/email
  email         TEXT,
  flowwow_shop_id TEXT,                  -- id магазина на Flowwow (если есть)
  flowwow_direct_link TEXT,              -- прямая ссылка магазина (zero-commission)
  commission_pct REAL NOT NULL DEFAULT 10.0,  -- наша комиссия с заказов по ref
  subscription  TEXT NOT NULL DEFAULT 'none',  -- none / active / paused
  subscribed_until TEXT,
  card_balance  INTEGER NOT NULL DEFAULT 0,    -- сколько карточек осталось
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- QR-карточки для букетов
CREATE TABLE IF NOT EXISTS cards (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  code       TEXT NOT NULL UNIQUE,       -- короткий код в QR
  shop_id    INTEGER NOT NULL REFERENCES shops(id),
  gift_id    TEXT REFERENCES gifts(id),  -- NULL пока не использована
  status     TEXT NOT NULL DEFAULT 'new', -- new / reserved / used
  printed_at TEXT,
  used_at    TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cards_shop ON cards(shop_id);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);

-- заказы букетов и комиссии (CPS-учёт)
CREATE TABLE IF NOT EXISTS orders (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  gift_id        TEXT REFERENCES gifts(id),
  shop_id        INTEGER NOT NULL REFERENCES shops(id),
  flowwow_order_id TEXT,                 -- id заказа на стороне Flowwow (если получили)
  bouquet_id     INTEGER REFERENCES bouquets(id),
  ref_code       TEXT,                   -- наш ref: scena_<gift_id>_<ts>
  amount         REAL NOT NULL,          -- сумма заказа, ₽
  commission_pct REAL NOT NULL,
  commission_rub REAL NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending', -- pending / clicked / confirmed / paid / cancelled
  clicked_at     TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_shop ON orders(shop_id);
CREATE INDEX IF NOT EXISTS idx_orders_ref ON orders(ref_code);

-- ---------- каталог букетов (кэш витрины агрегаторов, напр. Flowwow) ----------
CREATE TABLE IF NOT EXISTS bouquets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  city        TEXT NOT NULL,
  title       TEXT NOT NULL,
  price_rub   REAL NOT NULL,
  photo_url   TEXT,
  flowwow_url TEXT NOT NULL,             -- ссылка на товар у агрегатора
  shop_name   TEXT,
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bouquets_city ON bouquets(city);

-- ---------- календарь важных дат ----------
CREATE TABLE IF NOT EXISTS important_dates (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  name        TEXT NOT NULL,            -- имя близкого
  date        TEXT NOT NULL,            -- MM-DD (ежегодно)
  remind_days INTEGER NOT NULL DEFAULT 7,
  notify_year INTEGER,                   -- в каком году уже напоминали (сброс каждый год)
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- закладки и списки сцен ----------
CREATE TABLE IF NOT EXISTS bookmarks (
  user_id  INTEGER NOT NULL REFERENCES users(id),
  scene_id INTEGER NOT NULL REFERENCES scenes(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, scene_id)
);
CREATE TABLE IF NOT EXISTS scene_lists (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS list_scenes (
  list_id  INTEGER NOT NULL REFERENCES scene_lists(id) ON DELETE CASCADE,
  scene_id INTEGER NOT NULL REFERENCES scenes(id),
  PRIMARY KEY (list_id, scene_id)
);

-- ---------- очередь уведомлений (бот/шедулер рассылает) ----------
-- Кандидаты таймкодов из внешних библиотек (OpenSubtitles/OPUS, GitHub SRT и т.п.).
-- Правило: НЕ храним текст субтитров/реплик — только время, отпечаток (sha1) и источник+лицензию.
-- Одобряет только админ после ручной проверки в легальном плеере (тогда verified=1 у сцены).
CREATE TABLE IF NOT EXISTS timecode_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene_id INTEGER NOT NULL REFERENCES scenes(id),
  service TEXT NOT NULL DEFAULT 'opensubtitles',   -- плеер/версия фильма, к которой привязан таймкод
  timecode_label TEXT NOT NULL,                     -- "1:23:45" (начало отрезка)
  timecode_seconds INTEGER NOT NULL,
  timecode_end_label TEXT,                          -- конец отрезка (из SRT end или расширенного окна)
  timecode_end_seconds INTEGER,
  source TEXT NOT NULL,                             -- URL/файл субтитров
  source_license TEXT NOT NULL,                     -- лицензия источника
  evidence_sha1 TEXT NOT NULL,                      -- sha1 нормализованной совпавшей строки субтитров (без хранения текста)
  line_index INTEGER,                               -- номер блока субтитров (для ручной перепроверки)
  match_score REAL DEFAULT 0,
  moment_label TEXT,                                -- короткая метка НАШИМИ словами (без реплик), заполняет админ при approve
  confidence REAL DEFAULT 0,                        -- уверенность: семантический скор × консенсус версий субтитров
  consensus INTEGER DEFAULT 1,                      -- сколько независимых файлов субтитров agree по этому моменту (±10 сек)
  auto INTEGER DEFAULT 0,                           -- 1 = консенсус высокий, можно авто-одобрить
  category TEXT,                                    -- интент, под который найден фрагмент
  fragment_type TEXT,                               -- фактический тип фрагмента из структуры SRT-окна
  evidence_features TEXT,                           -- JSON признаков (БЕЗ текста реплик)
  reason TEXT,                                      -- человекочитаемое «почему этот момент»
  intent_score REAL DEFAULT 0,                      -- итоговый скор категории+структуры
  status TEXT NOT NULL DEFAULT 'pending',           -- pending / approved / rejected / archived
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  reviewed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_tc_candidates_scene ON timecode_candidates(scene_id);
CREATE INDEX IF NOT EXISTS idx_tc_candidates_status ON timecode_candidates(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tc_candidates_dedup ON timecode_candidates(scene_id, source, timecode_seconds);

-- события аналитики воронки (gift_created/opened/replied, card_scan, …)
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref TEXT,                -- gift_id / card code / scene_id
  telegram_id INTEGER,
  meta TEXT,               -- JSON
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- жалобы на подарки (abuse): модерация видит в админке
CREATE TABLE IF NOT EXISTS gift_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gift_id TEXT NOT NULL REFERENCES gifts(id),
  reason TEXT NOT NULL,
  reporter_telegram_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open',   -- open / reviewed
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gift_reports_status ON gift_reports(status);

CREATE TABLE IF NOT EXISTS notifications (
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id INTEGER NOT NULL,
  kind        TEXT NOT NULL,             -- reply / reminder / system
  text        TEXT NOT NULL,
  url         TEXT,
  sent        INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_pending ON notifications(sent, telegram_id);

-- ---------- платежи (ЮKassa; в dev — внутреннее подтверждение) ----------
CREATE TABLE IF NOT EXISTS payments (
  id               TEXT PRIMARY KEY,     -- наш id платежа (metadata для ЮKassa)
  user_id          INTEGER REFERENCES users(id),
  shop_id          INTEGER REFERENCES shops(id),
  kind             TEXT NOT NULL,        -- card_pack / subscription / premium_film
  payload          TEXT NOT NULL DEFAULT '{}',  -- JSON: {count, ...}
  amount_rub       REAL NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending', -- pending / paid / cancelled / refunded
  provider         TEXT NOT NULL DEFAULT 'dev',     -- dev / yookassa
  provider_payment_id TEXT,              -- id платежа у провайдера
  confirmation_url TEXT,                 -- куда вести плательщика
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  paid_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_shop ON payments(shop_id);

-- ---------- согласия (152-ФЗ) ----------
CREATE TABLE IF NOT EXISTS consents (
  policy_version TEXT,
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER REFERENCES users(id),
  gift_id      TEXT REFERENCES gifts(id),
  consent_type TEXT NOT NULL,           -- pd_processing / voice_recording / minor_data
  granted      INTEGER NOT NULL DEFAULT 1,
  ip           TEXT,
  user_agent   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Shared, short-lived fixed-window anti-spam counters (hashed subjects).
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    subject TEXT NOT NULL,
    scope TEXT NOT NULL,
    bucket INTEGER NOT NULL,
    hits INTEGER NOT NULL,
    PRIMARY KEY (subject, scope, bucket)
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_bucket ON rate_limit_buckets(bucket);

-- Operational audit contains no request bodies, tokens or message text.
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  resource TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE TABLE IF NOT EXISTS worker_state (
  name TEXT PRIMARY KEY,
  heartbeat_at TEXT NOT NULL,
  failed_tasks INTEGER NOT NULL DEFAULT 0,
  delivery_enabled INTEGER NOT NULL DEFAULT 0
);
