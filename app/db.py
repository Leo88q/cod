"""SQLite-слой: инициализация схемы, миграции, сид, помощники."""
import json
import os
import secrets
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(BASE_DIR, "db", "schema.sql")
SEED_SCENES_PATH = os.path.join(BASE_DIR, "db", "seed_scenes.sql")
SEED_SCENES_MORE_PATH = os.path.join(BASE_DIR, "db", "seed_scenes_more.sql")
SEED_CATALOG_PATH = os.path.join(BASE_DIR, "db", "seed_catalog.sql")
SEED_BOUQUETS_PATH = os.path.join(BASE_DIR, "db", "seed_bouquets.sql")

# Миграции для уже существующей dev-БД: (таблица, колонка, определение)
MIGRATIONS = [
    ("scenes", "imdb_id", "TEXT"),
    ("scenes", "tmdb_id", "TEXT"),
    ("consents", "policy_version", "TEXT"),
    ("connections", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("connections", "requested_by", "INTEGER REFERENCES users(id)"),
    ("shops", "owner_user_id", "INTEGER REFERENCES users(id)"),
    ("notifications", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("notifications", "next_attempt_at", "TEXT"),
    ("users", "phone", "TEXT"),
    ("users", "lang", "TEXT"),
    ("users", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
    ("scenes", "watch_url", "TEXT"),
    ("scenes", "poster_url", "TEXT"),
    ("scenes", "popularity", "INTEGER NOT NULL DEFAULT 0"),
    ("gifts", "title", "TEXT"),
    ("gifts", "expires_at", "TEXT"),
    ("gifts", "view_count", "INTEGER NOT NULL DEFAULT 0"),
    ("replies", "media_path", "TEXT"),
    ("orders", "bouquet_id", "INTEGER"),
    ("orders", "ref_code", "TEXT"),
    ("orders", "clicked_at", "TEXT"),
    ("important_dates", "notify_year", "INTEGER"),
    ("shops", "email", "TEXT"),
    ("users", "premium_until", "TEXT"),
    ("gifts", "recipient_city", "TEXT"),
    ("gifts", "theme", "TEXT NOT NULL DEFAULT 'cinema'"),
    ("gifts", "soon_notified", "INTEGER NOT NULL DEFAULT 0"),
    ("gifts", "inactive_at", "TEXT"),
    ("gift_scenes", "timecode_label", "TEXT"),
    ("gift_scenes", "timecode_seconds", "INTEGER"),
    ("gift_scenes", "timecode_end_label", "TEXT"),
    ("gift_scenes", "timecode_end_seconds", "INTEGER"),
    ("scenes", "timecode_end_seconds", "INTEGER"),
    ("scenes", "timecode_end_label", "TEXT"),
    ("timecode_candidates", "timecode_end_label", "TEXT"),
    ("timecode_candidates", "timecode_end_seconds", "INTEGER"),
    ("timecode_candidates", "moment_label", "TEXT"),
    ("timecode_candidates", "confidence", "REAL DEFAULT 0"),
    ("timecode_candidates", "consensus", "INTEGER DEFAULT 1"),
    ("timecode_candidates", "auto", "INTEGER DEFAULT 0"),
]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: параллельное чтение во время записи (prod-нагрузка)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, coldef in MIGRATIONS:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
    # уникальный индекс каталога (для INSERT OR IGNORE в seed_catalog.sql)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_scenes_title_year ON scenes(movie_title, movie_year)")
    except sqlite3.OperationalError:
        pass  # в старой БД уже есть дубли — индекс не создастся, сид просто пропустит дубли по title через OR IGNORE не сработает; ок для dev
    # таблица кандидатов таймкодов (миграция для старых БД)
    conn.execute("""CREATE TABLE IF NOT EXISTS timecode_candidates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      scene_id INTEGER NOT NULL REFERENCES scenes(id),
      service TEXT NOT NULL DEFAULT 'opensubtitles',
      timecode_label TEXT NOT NULL,
      timecode_seconds INTEGER NOT NULL,
      timecode_end_label TEXT,
      timecode_end_seconds INTEGER,
      source TEXT NOT NULL,
      source_license TEXT NOT NULL,
      evidence_sha1 TEXT NOT NULL,
      line_index INTEGER,
      match_score REAL DEFAULT 0,
      moment_label TEXT,
      confidence REAL DEFAULT 0,
      consensus INTEGER DEFAULT 1,
      auto INTEGER DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL,
      reviewed_at TEXT,
      reviewed_by TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tc_candidates_scene ON timecode_candidates(scene_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS gift_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gift_id TEXT NOT NULL REFERENCES gifts(id),
      reason TEXT NOT NULL,
      reporter_telegram_id INTEGER,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_reports_status ON gift_reports(status)")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL,
      ref TEXT,
      telegram_id INTEGER,
      meta TEXT,
      created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tc_candidates_status ON timecode_candidates(status)")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tc_candidates_dedup ON timecode_candidates(scene_id, source, timecode_seconds)")
    except sqlite3.OperationalError:
        pass


def init_db(db_path: str, with_seed: bool = True) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = connect(db_path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _migrate(conn)
    # Reconcile balances from the actual issued, unused cards, including older databases.
    conn.execute("UPDATE shops SET card_balance=(SELECT COUNT(*) FROM cards WHERE cards.shop_id=shops.id AND cards.status<>'used')")
    if with_seed:
        if conn.execute("SELECT COUNT(*) AS c FROM scenes").fetchone()["c"] == 0:
            with open(SEED_SCENES_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
            if os.path.exists(SEED_SCENES_MORE_PATH):
                with open(SEED_SCENES_MORE_PATH, encoding="utf-8") as f:
                    conn.executescript(f.read())
        # большой каталог — всегда, идемпотентно (INSERT OR IGNORE + unique(title, year))
        if os.path.exists(SEED_CATALOG_PATH):
            with open(SEED_CATALOG_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
        if conn.execute("SELECT COUNT(*) AS c FROM bouquets").fetchone()["c"] == 0:
            with open(SEED_BOUQUETS_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
    # Ранний сид хранил строку '0:00:00' как ЗАГЛУШКУ, а не реальный таймкод.
    # Не показываем её пользователю и не считаем «с таймкодом»: реальное время появляется
    # только после одобрения кандидата в админке (timecode_seconds + verified=1).
    conn.execute(
        "UPDATE scenes SET timecode_label=NULL, timecode_seconds=NULL "
        " WHERE timecode_label='0:00:00' AND timecode_seconds IS NULL AND verified=0"
    )
    conn.commit()
    conn.close()


def new_code(n: int = 8) -> str:
    """Короткий url-safe код для deeplink/QR."""
    return secrets.token_urlsafe(n)[:n]


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("mood_tags", "occasion_tags"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key] = []
    return d


def enqueue_notification(telegram_id: int, kind: str, text: str, url: str | None = None, conn=None) -> None:
    """Use caller transaction when supplied (scheduler must not open nested writers)."""
    owned = conn is None
    if owned:
        conn = connect(os.environ.get("DB_PATH", os.path.join(BASE_DIR, "scena.db")))
    try:
        conn.execute("INSERT INTO notifications (telegram_id, kind, text, url) VALUES (?,?,?,?)",
                     (telegram_id, kind, text, url))
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
