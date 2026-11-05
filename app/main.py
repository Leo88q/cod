"""«Сцена» — FastAPI-бэкенд (продакт-версия).

Запуск:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Бот отдельным процессом: BOT_TOKEN=... python -m app.bot
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import bot as bot_module
from . import config, payments, scheduler, search as semantic
from .db import connect, init_db, new_code, row_to_dict, enqueue_notification
from .flowwow import watch_links, order_link
from .qr import qr_svg
from .security import request_user, require_admin, log_consent, require_self, require_shop, require_metrics

init_db(config.DB_PATH)
os.makedirs(config.MEDIA_DIR, exist_ok=True)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from aiogram import Bot
    if config.WEBHOOK_URL and not config.WEBHOOK_SECRET:
        raise RuntimeError("WEBHOOK_SECRET is required when WEBHOOK_URL is configured")
    if config.BOT_TOKEN:
        bot_module.bot = Bot(config.BOT_TOKEN)
        if config.WEBHOOK_URL:
            # prod: webhook вместо polling
            await bot_module.bot.set_webhook(
                config.WEBHOOK_URL.rstrip("/") + "/tg/webhook",
                secret_token=config.WEBHOOK_SECRET or None,
            )
    task = asyncio.create_task(scheduler.run(bot_module.bot)) if config.SCHEDULER_ENABLED else None
    yield
    if task:
        task.cancel()
    if bot_module.bot:
        if config.WEBHOOK_URL:
            await bot_module.bot.delete_webhook()
        await bot_module.bot.session.close()


app = FastAPI(title="Сцена", version="1.0.0", lifespan=lifespan)
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if not _cors_origins:
    # prod: только наш публичный домен; dev: локалхост
    pub = (config.PUBLIC_URL or "").rstrip("/")
    _cors_origins = [pub] if pub and "localhost" not in pub and "127.0.0.1" not in pub else [
        "http://localhost:8000", "http://127.0.0.1:8000", pub or "http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.post("/tg/webhook", include_in_schema=False)
async def tg_webhook(request: Request):
    """Webhook от Telegram (prod-режим бота)."""
    if not config.WEBHOOK_SECRET or \
            request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret")
    if not bot_module.bot:
        raise HTTPException(503, "bot not configured")
    from aiogram.types import Update
    update = Update.model_validate(await request.json())
    await bot_module.dp.feed_update(bot_module.bot, update)
    return {"ok": True}


# ---------- helpers ----------
def now() -> datetime:
    return datetime.now(timezone.utc)


def track_event(conn, kind: str, ref: Optional[str] = None, telegram_id: Optional[int] = None, meta: Optional[dict] = None):
    """Событие аналитики (воронка). Пишем в events."""
    import json as _json
    conn.execute("INSERT INTO events (kind, ref, telegram_id, meta, created_at) VALUES (?,?,?,?,?)",
                 (kind, ref, telegram_id, _json.dumps(meta or {}, ensure_ascii=False), now_iso()))


def now_iso() -> str:
    return now().isoformat()


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_db(s: str) -> datetime:
    """Дата из SQLite (naive UTC) -> aware datetime."""
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def app_url(request: Request, hash_path: str = "") -> str:
    return config.webapp_url(base_url(request)) + ("/#" + hash_path if hash_path else "")


def gift_deeplink(code: str) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?startapp=g_{code}"


def public_scene(conn, scene_id: int, full: bool = False) -> Optional[dict]:
    cols = ("id, movie_title, movie_year, service, version_note, timecode_label, timecode_seconds,"
            " timecode_end_label, timecode_end_seconds, watch_url, poster_url, genre, era, age_rating,"
            " mood_tags, occasion_tags, description, why_fits, verified") if full else (
        "id, movie_title, movie_year, service, version_note, timecode_label, timecode_seconds,"
        " timecode_end_label, timecode_end_seconds, watch_url, poster_url, genre, era, age_rating,"
        " mood_tags, occasion_tags, description, why_fits")
    row = conn.execute(
        f"SELECT {cols} FROM scenes WHERE id = ? AND status = 'approved'", (scene_id,)
    ).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    d["watch_links"] = watch_links(d)
    return d


from .media_access import signed_url as media_url, verify as verify_media


# ---------- модели ----------
class SceneSearch(BaseModel):
    occasion: Optional[str] = None
    mood: Optional[str] = None
    query: Optional[str] = None
    genre: Optional[str] = None
    era: Optional[str] = None
    age: Optional[str] = None
    verified_only: bool = False
    has_timecode: bool = False
    sort: str = "popular"  # popular | year_desc | year_asc | title
    limit: int = Field(default=7, le=50)


class SceneSuggest(BaseModel):
    movie_title: str
    movie_year: Optional[int] = None
    service: Optional[str] = None
    timecode_label: Optional[str] = None
    occasion_tags: list[str] = Field(default_factory=list)
    mood_tags: list[str] = Field(default_factory=list)
    description: str
    telegram_id: Optional[int] = None


def tc_label_to_seconds(label: str) -> Optional[int]:
    """'1:23:45' -> 5025 (или None)."""
    try:
        parts = [int(x) for x in str(label).strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 1:
            return parts[0]
    except (ValueError, AttributeError):
        return None
    return None


class GiftCreate(BaseModel):
    giver_telegram_id: Optional[int] = None
    recipient_name: str = Field(min_length=1, max_length=80)
    recipient_city: Optional[str] = None
    title: Optional[str] = None
    occasion: Optional[str] = None
    tone: Optional[str] = None
    scene_ids: list[int] = Field(default_factory=list, max_length=14)
    # выбор момента клиентом: {"<scene_id>": "<timecode_label>"} — если у сцены несколько
    # одобренных моментов; выбранный таймкод фиксируется снапшотом в gift_scenes
    moments: Optional[dict] = None
    message_text: Optional[str] = None
    message_voice: Optional[str] = None   # telegram file_id или /media/...
    message_photo: Optional[str] = None
    format: str = "single"
    series_days: int = 1
    open_at: Optional[str] = None
    card_code: Optional[str] = None
    theme: str = "cinema"
    consent: bool = False  # согласие на обработку ПДн (152-ФЗ), чекбокс в форме; обязателен в prod


    @field_validator("open_at")
    @classmethod
    def validate_open_time(cls, value):
        if value is None:
            return value
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timezone required")
            return stamp.astimezone(timezone.utc).isoformat()
        except ValueError:
            raise ValueError("open_at должен быть ISO8601 с часовым поясом")


class ReplyCreate(BaseModel):
    respondent: Optional[str] = None
    respondent_telegram_id: Optional[int] = None
    type: str
    scene_id: Optional[int] = None
    reaction: Optional[str] = None
    message_text: Optional[str] = None
    media_path: Optional[str] = None


class NoteCreate(BaseModel):
    author_telegram_id: Optional[int] = None
    text: Optional[str] = None
    photo: Optional[str] = None


class BouquetOrder(BaseModel):
    bouquet_id: int
    amount: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)


class ShopCreate(BaseModel):
    name: str
    city: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None
    flowwow_direct_link: Optional[str] = None


class CardsOrder(BaseModel):
    count: int = Field(default=50, ge=1, le=10000)


class OrderRegister(BaseModel):
    shop_id: int
    amount: float = Field(ge=0, allow_inf_nan=False)
    flowwow_order_id: Optional[str] = None
    gift_id: Optional[str] = None


class DateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    date: str  # MM-DD
    remind_days: int = Field(default=7, ge=0, le=365)
    telegram_id: Optional[int] = None


class ConnectionCreate(BaseModel):
    peer_telegram_id: int
    telegram_id: Optional[int] = None


class Bookmark(BaseModel):
    scene_id: int
    telegram_id: Optional[int] = None


class SceneStatus(BaseModel):
    status: str  # approved / rejected / draft


# ---------- базовое ----------
from starlette.concurrency import run_in_threadpool
from .rate_limit import consume, verified_subject


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Shared IP + verified-user quotas; provider webhooks authenticate separately."""
    path = request.url.path
    if request.method == "POST" and path.startswith("/api/") and not path.endswith("/webhook"):
        ip = request.client.host if request.client else "anon"
        general = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
        quotas = [(f"ip:{ip}", "all", general)]
        user = verified_subject(request.headers.get("X-Telegram-Init-Data", ""))
        if user:
            quotas.append((user, "all", int(os.getenv("RATE_LIMIT_USER_PER_MIN", str(general)))))
        scope = None
        if path == "/api/gifts":
            scope, limit = "gifts", int(os.getenv("RATE_LIMIT_GIFTS_PER_MIN", "10"))
        elif path == "/api/scenes/suggest":
            scope, limit = "suggest", int(os.getenv("RATE_LIMIT_SUGGEST_PER_MIN", "5"))
        elif path == "/api/upload":
            scope, limit = "upload", int(os.getenv("RATE_LIMIT_UPLOAD_PER_MIN", "10"))
        if scope:
            quotas.append((f"ip:{ip}", scope, limit))
            if user:
                quotas.append((user, scope, limit))
        retry = await run_in_threadpool(consume, config.DB_PATH, quotas)
        if retry:
            return JSONResponse(
                {"detail": "Слишком много запросов — подожди минутку 🙏", "retry_after": retry},
                status_code=429, headers={"Retry-After": str(retry)})
    return await call_next(request)


from .observability import install as install_diagnostics, counts as request_counts
install_diagnostics(app)


@app.get("/api/admin/metrics")
def admin_metrics(request: Request):
    require_metrics(request)
    conn = connect(config.DB_PATH)
    try:
        pending = conn.execute("SELECT COUNT(*) FROM notifications WHERE sent=0").fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM notifications WHERE sent=-1").fetchone()[0]
        oldest = conn.execute("SELECT MIN(created_at) FROM notifications WHERE sent=0").fetchone()[0]
        stuck_payments = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE "
            "(status='creating' AND datetime(created_at)<datetime('now','-23 hours')) OR "
            "(status IN ('verifying_succeeded','verifying_canceled') AND datetime(created_at)<datetime('now','-10 minutes'))"
        ).fetchone()[0]
        oldest_age = max(0, int((now() - parse_db(oldest)).total_seconds())) if oldest else 0
        worker = worker_health(conn)
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"requests_this_process": dict(request_counts), "pending_notifications": pending,
            "failed_notifications": failed, "oldest_pending_seconds": oldest_age,
            "worker": worker, "database": "ok", "stuck_payment_creations": stuck_payments}


@app.get("/ready", include_in_schema=False)
def readiness():
    conn = connect(config.DB_PATH)
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ready"}


_LEGAL_PAGE = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — «Сцена»</title>
<style>body{{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;line-height:1.6;color:#1c1830}}
h1{{font-size:24px}}a{{color:#7c5cff}}</style></head><body>
<h1>{title}</h1>{body}<p><a href="/">← на главную</a></p></body></html>"""


@app.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
def privacy():
    from html import escape
    body = f"""
<p><b>Оператор:</b> {escape(config.OPERATOR_NAME)}; ИНН: {escape(config.OPERATOR_INN)}.
Контакт для обращений: {escape(config.OPERATOR_CONTACT)}. Версия политики: {escape(config.POLICY_VERSION)}.</p>
<p><b>Данные:</b> Telegram ID, имя/username, сообщения и вложения, подарки, ответы, заметки, важные даты,
согласия и технические данные, необходимые для работы сервиса и защиты от злоупотреблений.</p>
<p><b>Цели:</b> доставка подарков, ответы и таймлайн, напоминания, обслуживание студий и платежей.</p>
<p><b>Ограничения контента:</b> не публикуем видео, дословные реплики или субтитры фильмов.
Загрузки фото/аудио декодируются и перекодируются, метаданные фото удаляются.</p>
<p><b>Согласие:</b> запрашивается при создании подарка и фиксируется с версией политики.
Новые версии применяются к последующим согласиям; исторические записи без версии не считаются версионированными.</p>
<p><b>Хранение:</b> неоткрытые подарки переводятся в недоступное состояние после настроенного TTL
(по умолчанию 30 дней); это не означает автоматическое удаление всех записей.
Вложения удалённых/просроченных подарков очищаются через 7 дней, непривязанные файлы — через 48 часов.</p>
<p><b>Права субъекта (ст. 14 152-ФЗ):</b> доступ, уточнение, отзыв согласия, экспорт и удаление.
Экспорт и удаление профиля доступны в таймлайне. Студии перед удалением нужно передать другому владельцу,
ожидающие платежи — завершить или отменить. Финансовые записи сохраняются без привязки к профилю.
Резервные копии и данные платёжного провайдера обрабатываются отдельно по регламенту оператора.</p>
<p><b>Журнал безопасности:</b> успешные чувствительные действия фиксируются без содержимого сообщений и токенов.
Telegram-идентификатор в журнале псевдонимизирован хешем, это не анонимизация. Срок хранения журнала:
{config.AUDIT_RETENTION_DAYS} дней с очисткой worker. Удаление профиля не удаляет этот ограниченный журнал автоматически.</p>
<p><b>Регион размещения, заявленный оператором:</b> {escape(config.HOSTING_REGION)}.
Этот текст и технические функции не заменяют юридическую проверку и подтверждение фактического размещения.</p>"""
    return _LEGAL_PAGE.format(title="Политика обработки персональных данных (152-ФЗ)", body=body)


@app.get("/terms", include_in_schema=False, response_class=HTMLResponse)
def terms():
    return _LEGAL_PAGE.format(title="Пользовательское соглашение", body="""
<p>Используя «Сцену», вы соглашаетесь: дарить только реальным людям; не спамить и не оскорблять; не загружать чужие фото без
согласия; не использовать сервис для обхода авторских прав (мы не храним и не показываем видео и дословный текст фильмов —
только название, год, таймкод и ссылки на легальные плееры).</p>
<p>Бесплатно для дарителя и получателя. Платно: QR-карточки для студий (50 ₽/шт от 100 шт; подписка 990 ₽/мес), премиум
«ваш фильм» (499 ₽/год). Возврат — по правилам ЮKassa в течение 14 дней, если услуга не оказана.</p>
<p>По жалобе (abuse) подарок может быть удалён модерацией. Споры — по праву РФ, претензионный порядок.</p>""")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(STATIC_DIR, "landing.html"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "scena", "version": "1.0.0", "time": now_iso()}


@app.post("/api/auth/telegram")
def auth_telegram(request: Request, body: dict = None):
    """Валидация initData и upsert пользователя. Клиент шлёт initData также заголовком
    X-Telegram-Init-Data на каждый запрос."""
    init_data = (body or {}).get("init_data") or request.headers.get("X-Telegram-Init-Data", "")
    user = request_user(request, None)
    if not user and init_data:
        from .security import upsert_user_from_init_data
        user = upsert_user_from_init_data(init_data)
    if not user:
        if config.ALLOW_ANON:
            return {"user": None, "anon": True}
        raise HTTPException(401, "Нужен валидный Telegram initData")
    return {"user": user, "anon": False}


@app.get("/api/users/me")
def users_me(request: Request, telegram_id: Optional[int] = None):
    user = request_user(request, telegram_id)
    if not user:
        return {"user": None, "premium": False}
    premium = bool(user["premium_until"]) and parse_db(user["premium_until"]) > now()
    return {"user": user, "premium": premium}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Загрузка медиа (фото открытки, голосовое). Возвращает /media/<имя> путь.
    Prod-hardening: лимит размера (MAX_UPLOAD_MB), whitelist расширений, потоковая запись."""
    ext = os.path.splitext(file.filename or "")[1].lower() or ".bin"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".ogg", ".mp3", ".oga", ".m4a", ".wav", ".webm"):
        raise HTTPException(400, "Неподдерживаемый формат файла")
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    name = new_code(10) + ext
    os.makedirs(config.MEDIA_DIR, exist_ok=True)
    path = os.path.join(config.MEDIA_DIR, name)
    size = 0
    with open(path, "wb") as f:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                f.close()
                os.remove(path)
                raise HTTPException(413, f"Файл больше {config.MAX_UPLOAD_MB} МБ")
            f.write(chunk)
    from .uploads import normalize
    normalized_path = await run_in_threadpool(normalize, path, ext)
    return {"path": normalized_path}


# ---------- каталог сцен ----------
@app.post("/api/scenes/search")
def search_scenes(body: SceneSearch):
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT * FROM scenes WHERE status = 'approved'").fetchall()
    conn.close()
    q = (body.query or "").strip().lower()
    out = []
    for r in rows:
        d = row_to_dict(r)
        occ = [t.lower() for t in d["occasion_tags"]]
        mood = [t.lower() for t in d["mood_tags"]]
        if body.occasion and body.occasion.lower() not in occ:
            continue
        if body.mood and body.mood.lower() not in mood:
            continue
        if body.genre and (d["genre"] or "").lower() != body.genre.lower():
            continue
        if body.era and (d["era"] or "").lower() != body.era.lower():
            continue
        if body.age and (d["age_rating"] or "") != body.age:
            continue
        if body.verified_only and not d.get("verified"):
            continue
        if body.has_timecode and not d.get("timecode_label"):
            continue
        if q:
            hay = (d["movie_title"] + " " + d["description"] + " " + (d["why_fits"] or "") + " " +
                   " ".join(occ + mood)).lower()
            if q not in hay:
                continue
        out.append({
            "id": d["id"], "movie_title": d["movie_title"], "movie_year": d["movie_year"],
            "popularity": d["popularity"],
            "timecode_label": d["timecode_label"], "timecode_end_label": d.get("timecode_end_label"),
            "service": d["service"], "verified": d.get("verified", 0),
            "watch_url": d.get("watch_url"), "poster_url": d.get("poster_url"),
            "genre": d["genre"], "era": d["era"], "age_rating": d["age_rating"],
            "mood_tags": d["mood_tags"], "occasion_tags": d["occasion_tags"],
            "description": d["description"], "why_fits": d["why_fits"],
            "watch_links": watch_links(d),
        })
    if body.sort == "year_desc":
        out.sort(key=lambda s: -(s.get("movie_year") or 0))
    elif body.sort == "year_asc":
        out.sort(key=lambda s: (s.get("movie_year") or 0))
    elif body.sort == "title":
        out.sort(key=lambda s: s.get("movie_title", ""))
    else:
        out.sort(key=lambda s: -s.get("popularity", 0))
    return {"scenes": out[: body.limit], "total": len(out)}


@app.get("/api/scenes/meta")
def scenes_meta():
    """Категории каталога с подсчётами — чтобы строить фильтры из реальных данных."""
    import json as _json
    from collections import Counter
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT genre, era, age_rating, verified, timecode_label, mood_tags, occasion_tags"
                        " FROM scenes WHERE status='approved'").fetchall()
    conn.close()
    genres, eras, ages, occ, mood = Counter(), Counter(), Counter(), Counter(), Counter()
    verified = with_tc = 0
    for r in rows:
        if r["genre"]:
            genres[r["genre"]] += 1
        if r["era"]:
            eras[r["era"]] += 1
        if r["age_rating"]:
            ages[r["age_rating"]] += 1
        if r["verified"]:
            verified += 1
        if r["timecode_label"]:
            with_tc += 1
        for t in _json.loads(r["mood_tags"] or "[]"):
            mood[t] += 1
        for t in _json.loads(r["occasion_tags"] or "[]"):
            occ[t] += 1
    def top(c, n=24):
        return [{"tag": k, "count": v} for k, v in c.most_common(n)]
    return {
        "total": len(rows), "verified": verified, "with_timecode": with_tc,
        "genres": top(genres, 30), "eras": top(eras, 15), "ages": top(ages, 10),
        "occasions": top(occ), "moods": top(mood),
    }


class SemanticSearch(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    occasion: Optional[str] = None


@app.post("/api/scenes/semantic")
def scenes_semantic(body: SemanticSearch):
    """Поиск по смыслу: «сцена, где извиняются», «про первую любовь», «что-то смешное на 8 марта»."""
    conn = connect(config.DB_PATH)
    hits = semantic.semantic_search(conn, body.query, limit=50)
    rows = {r["id"]: r for r in conn.execute(
        "SELECT * FROM scenes WHERE status='approved'").fetchall()}
    conn.close()
    out = []
    for h in hits:
        r = rows.get(h["scene_id"])
        if not r:
            continue
        d = row_to_dict(r)
        if body.occasion and body.occasion.lower() not in [t.lower() for t in d["occasion_tags"]]:
            continue
        out.append({
            "id": d["id"], "movie_title": d["movie_title"], "movie_year": d["movie_year"],
            "timecode_label": d["timecode_label"], "timecode_end_label": d.get("timecode_end_label"),
            "service": d["service"], "verified": d.get("verified", 0),
            "genre": d["genre"], "era": d["era"], "age_rating": d["age_rating"],
            "mood_tags": d["mood_tags"], "occasion_tags": d["occasion_tags"],
            "description": d["description"], "why_fits": d["why_fits"],
            "score": h["score"], "watch_links": watch_links(d),
        })
    return {"scenes": out[: body.limit], "total": len(out)}


@app.get("/api/scenes/daily")
def scene_of_the_day():
    """Случайная одобренная сцена — «сцена дня» для лендинга и бота."""
    import random
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT id FROM scenes WHERE status='approved'").fetchall()
    conn.close()
    if not rows:
        raise HTTPException(404, "Каталог пуст")
    sid = random.choice(rows)["id"]
    conn = connect(config.DB_PATH)
    s = public_scene(conn, sid)
    conn.close()
    return s


@app.get("/api/lists")
def get_lists():
    conn = connect(config.DB_PATH)
    lists = conn.execute("SELECT * FROM scene_lists ORDER BY id").fetchall()
    out = []
    for l in lists:
        sc = conn.execute(
            "SELECT s.id, s.movie_title, s.movie_year, s.timecode_label, s.genre, s.era, s.description"
            " FROM list_scenes ls JOIN scenes s ON s.id=ls.scene_id WHERE ls.list_id=? AND s.status='approved'",
            (l["id"],)).fetchall()
        out.append({"id": l["id"], "name": l["name"], "count": len(sc),
                    "scenes": [dict(x) for x in sc]})
    conn.close()
    return {"lists": out}


@app.get("/api/lists/{list_id}")
def get_list(list_id: int):
    conn = connect(config.DB_PATH)
    l = conn.execute("SELECT * FROM scene_lists WHERE id=?", (list_id,)).fetchone()
    if not l:
        conn.close()
        raise HTTPException(404, "Подборка не найдена")
    sc = conn.execute(
        "SELECT s.* FROM list_scenes ls JOIN scenes s ON s.id=ls.scene_id"
        " WHERE ls.list_id=? AND s.status='approved'", (list_id,)).fetchall()
    conn.close()
    return {"list": dict(l), "scenes": [row_to_dict(r) for r in sc]}


@app.get("/api/scenes/{scene_id}")
def get_scene(scene_id: int):
    conn = connect(config.DB_PATH)
    s = public_scene(conn, scene_id)
    conn.close()
    if not s:
        raise HTTPException(404, "Сцена не найдена")
    return s


@app.post("/api/scenes/suggest")
def suggest_scene(request: Request, body: SceneSuggest):
    """Пользовательское предложение сцены — уходит в модерацию (status=draft)."""
    user = request_user(request, body.telegram_id)
    conn = connect(config.DB_PATH)
    cur = conn.execute(
        "INSERT INTO scenes (movie_title, movie_year, service, timecode_label, occasion_tags, mood_tags,"
        " description, source, status, suggested_by) VALUES (?,?,?,?,?,?,?, 'user_suggestion', 'draft', ?)",
        (body.movie_title, body.movie_year, body.service, body.timecode_label,
         json.dumps(body.occasion_tags, ensure_ascii=False),
         json.dumps(body.mood_tags, ensure_ascii=False),
         body.description, user["id"] if user else None),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return {"ok": True, "scene_id": sid, "status": "draft",
            "message": "Спасибо! Сцена отправлена на модерацию 🎬"}


@app.post("/api/bookmarks")
def add_bookmark(request: Request, body: Bookmark):
    user = request_user(request, body.telegram_id)
    if not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    conn = connect(config.DB_PATH)
    conn.execute("INSERT OR IGNORE INTO bookmarks (user_id, scene_id) VALUES (?,?)",
                 (user["id"], body.scene_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/bookmarks/{scene_id}")
def del_bookmark(request: Request, scene_id: int, telegram_id: Optional[int] = None):
    user = request_user(request, telegram_id)
    if not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    conn = connect(config.DB_PATH)
    conn.execute("DELETE FROM bookmarks WHERE user_id=? AND scene_id=?", (user["id"], scene_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/bookmarks")
def list_bookmarks(request: Request, telegram_id: Optional[int] = None):
    user = request_user(request, telegram_id)
    if not user:
        return {"scenes": []}
    conn = connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT s.* FROM bookmarks b JOIN scenes s ON s.id = b.scene_id"
        " WHERE b.user_id = ? AND s.status='approved'", (user["id"],)).fetchall()
    conn.close()
    return {"scenes": [row_to_dict(r) for r in rows]}


# ---------- подарки ----------
@app.post("/api/gifts")
def create_gift(request: Request, body: GiftCreate):
    if not body.scene_ids:
        raise HTTPException(400, "Нужна хотя бы одна сцена (scene_ids)")
    if not config.ALLOW_ANON and not body.consent:
        raise HTTPException(400, "Нужно согласие на обработку персональных данных (consent=true, см. /privacy)")
    if body.format not in ("single", "series"):
        raise HTTPException(400, "format: single | series")
    if body.format == "series":
        body.series_days = max(1, min(body.series_days, 14))
        if len(body.scene_ids) != body.series_days:
            raise HTTPException(400, "Для серии нужно scene_ids по числу дней")
    user = request_user(request, body.giver_telegram_id)
    if not config.ALLOW_ANON and not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        giver_id = user["id"] if user else None
        card_id = None
        shop_id = None
        if body.card_code:
            card = conn.execute("SELECT * FROM cards WHERE code = ?", (body.card_code,)).fetchone()
            if not card:
                raise HTTPException(404, "Карточка не найдена")
            if card["status"] == "used":
                raise HTTPException(409, "Эта карточка уже использована")
            card_id = card["id"]
            shop_id = card["shop_id"]
        for sid in body.scene_ids:
            if not conn.execute("SELECT id FROM scenes WHERE id=? AND status='approved'", (sid,)).fetchone():
                raise HTTPException(400, f"Сцена {sid} не найдена или не одобрена")
        gift_id = new_code(8)
        expires = (now() + timedelta(days=config.GIFT_TTL_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO gifts (id, giver_id, card_id, shop_id, recipient_name, recipient_city, title,"
            " occasion, tone, message_text, message_voice_file_id, message_photo_file_id, format, theme,"
            " series_days, open_at, expires_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'created')",
            (gift_id, giver_id, card_id, shop_id, body.recipient_name, body.recipient_city, body.title,
             body.occasion, body.tone, body.message_text, body.message_voice, body.message_photo,
             body.format, body.theme, body.series_days if body.format == "series" else 1,
             body.open_at, expires),
        )
        for i, sid in enumerate(body.scene_ids):
            # снапшот таймкода: выбранный клиентом момент (из одобренных кандидатов) или основной
            # таймкод сцены. Открытка фиксирована: изменения каталога позже не влияют на подарок.
            sc = conn.execute("SELECT timecode_label, timecode_seconds, timecode_end_label, timecode_end_seconds"
                              " FROM scenes WHERE id=?", (sid,)).fetchone()
            snap_label, snap_sec = (sc["timecode_label"], sc["timecode_seconds"]) if sc else (None, None)
            snap_end_label, snap_end_sec = (sc["timecode_end_label"], sc["timecode_end_seconds"]) if sc else (None, None)
            chosen = (body.moments or {}).get(str(sid))
            if chosen:
                cand = conn.execute(
                    "SELECT timecode_label, timecode_seconds, timecode_end_label, timecode_end_seconds"
                    " FROM timecode_candidates"
                    " WHERE scene_id=? AND status='approved' AND timecode_label=?", (sid, chosen)).fetchone()
                if cand:
                    snap_label, snap_sec = cand["timecode_label"], cand["timecode_seconds"]
                    snap_end_label, snap_end_sec = cand["timecode_end_label"], cand["timecode_end_seconds"]
                elif chosen != snap_label:
                    conn.rollback()
                    raise HTTPException(400, "Выбранный момент не одобрен")
            conn.execute("INSERT INTO gift_scenes (gift_id, scene_id, day_index, timecode_label, timecode_seconds,"
                         " timecode_end_label, timecode_end_seconds) VALUES (?,?,?,?,?,?,?)",
                         (gift_id, sid, i, snap_label, snap_sec, snap_end_label, snap_end_sec))
            conn.execute("UPDATE scenes SET popularity = popularity + 1 WHERE id = ?", (sid,))
        if card_id:
            conn.execute("UPDATE cards SET status='used', gift_id=?, used_at=? WHERE id=?",
                         (gift_id, now_iso(), card_id))
            conn.execute("UPDATE shops SET card_balance = MAX(0, card_balance - 1) WHERE id=?", (shop_id,))
        log_consent(gift_id, giver_id, "pd_processing", request, conn=conn)
        track_event(conn, "gift_created", gift_id, user["telegram_id"] if user else None,
                    {"occasion": body.occasion, "format": body.format, "theme": body.theme,
                     "scenes": len(body.scene_ids), "card": bool(card_id)})
        conn.commit()
    finally:
        conn.close()
    return {
        "gift_id": gift_id,
        "open_url": app_url(request, f"/g/{gift_id}"),
        "deeplink": gift_deeplink(gift_id),
        "qr": f"{base_url(request)}/api/gifts/{gift_id}/qr.svg",
        "card_used": card_id is not None,
    }


def _serialize_gift(conn, g: dict) -> dict:
    gs = conn.execute(
        "SELECT gs.day_index, gs.timecode_label AS snap_tc_label, gs.timecode_seconds AS snap_tc_seconds,"
        " gs.timecode_end_label AS snap_tc_end_label, gs.timecode_end_seconds AS snap_tc_end_seconds, s.*"
        " FROM gift_scenes gs JOIN scenes s ON s.id = gs.scene_id"
        " WHERE gs.gift_id = ? ORDER BY gs.day_index", (g["id"],)).fetchall()
    scenes = []
    for r in gs:
        d = row_to_dict(r)
        s = {
            "day_index": d["day_index"], "id": d["id"], "movie_title": d["movie_title"],
            "movie_year": d["movie_year"],
            # фиксированный снапшот таймкода открытки (приоритетнее текущего в каталоге)
            "timecode_label": d.get("snap_tc_label"),
            "timecode_seconds": d.get("snap_tc_seconds"),
            "timecode_end_label": d.get("snap_tc_end_label"),
            "timecode_end_seconds": d.get("snap_tc_end_seconds"),
            "service": d["service"], "watch_url": d.get("watch_url"), "poster_url": d.get("poster_url"),
            "genre": d["genre"], "era": d["era"], "description": d["description"],
            "why_fits": d["why_fits"], "mood_tags": d["mood_tags"],
            "occasion_tags": d["occasion_tags"],
        }
        # A direct link can point to a different moment after a catalog edit.
        link_scene = dict(d)
        if (s["timecode_label"], s["timecode_seconds"]) != (d["timecode_label"], d["timecode_seconds"]):
            link_scene["watch_url"] = None
            s["watch_url"] = None
        s["watch_links"] = watch_links(link_scene)
        scenes.append(s)
    # серия: открываем по одной сцене в день
    if g["format"] == "series":
        created = parse_db(g["created_at"])
        start = max(created, parse_db(g["open_at"])) if g["open_at"] else created
        days_passed = min(max(0, (now() - start).days + 1), max(g["series_days"], len(scenes)))
        visible = [s for s in scenes if s["day_index"] < days_passed]
        locked = len(scenes) - len(visible)
    else:
        visible, locked = scenes, 0
    replies = conn.execute(
        "SELECT r.*, s.movie_title, s.timecode_label, s.description AS scene_description,"
        " s.poster_url AS scene_poster FROM replies r LEFT JOIN scenes s ON s.id = r.scene_id"
        " WHERE r.gift_id = ? ORDER BY r.created_at", (g["id"],)).fetchall()
    notes = conn.execute("SELECT * FROM gift_notes WHERE gift_id = ? ORDER BY created_at",
                          (g["id"],)).fetchall()
    return {
        "gift": {**g,
                 "message_photo_url": media_url(g["message_photo_file_id"], g["id"]),
                 "message_voice_url": media_url(g["message_voice_file_id"], g["id"])},
        "scenes": visible,
        "locked_count": locked,
        "replies": [{**dict(r), "media_url": media_url(r["media_path"], g["id"]),
                     "photo_url": media_url(r["photo_file_id"], g["id"]),
                     "voice_url": media_url(r["voice_file_id"], g["id"])} for r in replies],
        "notes": [dict(n) for n in notes],
    }


@app.get("/api/gifts/{gift_id}")
def get_gift(gift_id: str):
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        g = conn.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
        if not g:
            raise HTTPException(404, "Подарок не найден")
        gd = dict(g)
        if gd["status"] == "expired":
            raise HTTPException(410, "Срок действия подарка истёк")
        if gd["status"] == "deleted":
            raise HTTPException(404, "Подарок удалён")
        # отложенное открытие
        if gd["open_at"]:
            try:
                if parse_iso(gd["open_at"]) > now():
                    return {"locked": True, "open_at": gd["open_at"],
                            "gift": {"id": gd["id"], "recipient_name": gd["recipient_name"],
                                     "title": gd["title"], "occasion": gd["occasion"]}}
            except ValueError:
                pass
        conn.execute("UPDATE gifts SET view_count = view_count + 1 WHERE id = ?", (gift_id,))
        gd["view_count"] = (gd["view_count"] or 0) + 1
        if gd["status"] == "created":
            conn.execute("UPDATE gifts SET status='opened', opened_at=? WHERE id=?", (now_iso(), gift_id))
            gd["status"] = "opened"
            gd["opened_at"] = now_iso()
            track_event(conn, "gift_opened", gift_id, None, {"views": gd["view_count"]})
            # Уведомление и переход состояния фиксируются одной транзакцией.
            if gd["giver_id"]:
                giver = conn.execute("SELECT telegram_id FROM users WHERE id=?", (gd["giver_id"],)).fetchone()
                if giver and giver["telegram_id"]:
                    enqueue_notification(giver["telegram_id"], "gift_opened",
                                         f"🎉 {gd['recipient_name']} открыл(а) ваш подарок со сценой из фильма!", conn=conn)
        data = _serialize_gift(conn, gd)
        conn.commit()
    finally:
        conn.close()
    data["locked"] = False
    return data


class GiftReport(BaseModel):
    reason: str
    telegram_id: Optional[int] = None


@app.post("/api/gifts/{gift_id}/report")
def report_gift(gift_id: str, body: GiftReport):
    """Пожаловаться на подарок (abuse/спам) — уходит в админку."""
    conn = connect(config.DB_PATH)
    g = conn.execute("SELECT id FROM gifts WHERE id=?", (gift_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "Подарок не найден")
    conn.execute("INSERT INTO gift_reports (gift_id, reason, reporter_telegram_id, created_at) VALUES (?,?,?,?)",
                 (gift_id, body.reason[:500], body.telegram_id, now_iso()))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/gifts/{gift_id}/delete")
def delete_gift(gift_id: str, request: Request, telegram_id: Optional[int] = None):
    """Мягкое удаление подарка — только дарителем (по telegram_id)."""
    user = request_user(request, telegram_id)
    conn = connect(config.DB_PATH)
    g = conn.execute("SELECT * FROM gifts WHERE id=?", (gift_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "Подарок не найден")
    if not config.ALLOW_ANON:
        if not user or g["giver_id"] != user["id"]:
            conn.close()
            raise HTTPException(403, "Удалить может только даритель")
    else:
        # dev/anon: позволяем, если совпал giver_telegram_id из запроса
        if telegram_id and g["giver_id"]:
            giver = conn.execute("SELECT telegram_id FROM users WHERE id=?", (g["giver_id"],)).fetchone()
            if not giver or giver["telegram_id"] != telegram_id:
                conn.close()
                raise HTTPException(403, "Удалить может только даритель")
    conn.execute("UPDATE gifts SET status='deleted', inactive_at=COALESCE(inactive_at, ?) WHERE id=?", (now_iso(), gift_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/admin/reports")
def admin_reports(request: Request, status: str = "open"):
    require_admin(request)
    conn = connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT r.*, g.recipient_name, g.occasion FROM gift_reports r LEFT JOIN gifts g ON g.id=r.gift_id"
        " WHERE r.status=? ORDER BY r.created_at DESC", (status,)).fetchall()
    conn.close()
    return {"reports": [dict(r) for r in rows]}


@app.post("/api/admin/reports/{rid}/close")
def admin_report_close(request: Request, rid: int):
    require_admin(request)
    conn = connect(config.DB_PATH)
    conn.execute("UPDATE gift_reports SET status='reviewed' WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/gifts/{gift_id}/reply")
def add_reply(request: Request, gift_id: str, body: ReplyCreate):
    if body.type not in ("scene", "reaction", "message"):
        raise HTTPException(400, "type: scene | reaction | message")
    if body.type == "scene" and not body.scene_id:
        raise HTTPException(400, "для ответа сценой нужен scene_id")
    user = request_user(request, body.respondent_telegram_id)
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        g = conn.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
        if not g:
            raise HTTPException(404, "Подарок не найден")
        if g["status"] in ("deleted", "expired"):
            raise HTTPException(404, "Подарок недоступен")
        if g["open_at"] and parse_iso(g["open_at"]) > now():
            raise HTTPException(403, "Подарок ещё не открыт")
        conn.execute(
            "INSERT INTO replies (gift_id, respondent, respondent_user_id, type, scene_id, reaction,"
            " message_text, voice_file_id, photo_file_id, media_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (gift_id, body.respondent, user["id"] if user else None, body.type, body.scene_id,
             body.reaction, body.message_text, None, None, body.media_path),
        )
        if g["status"] == "created":
            conn.execute("UPDATE gifts SET opened_at=? WHERE id=?", (now_iso(), gift_id))
            track_event(conn, "gift_opened", gift_id, None, {"views": g["view_count"]})
        conn.execute("UPDATE gifts SET status='replied' WHERE id=?", (gift_id,))
        track_event(conn, "gift_replied", gift_id, user["telegram_id"] if user else None, {"type": body.type})
        if g["giver_id"]:
            giver = conn.execute("SELECT telegram_id FROM users WHERE id=?", (g["giver_id"],)).fetchone()
            if giver and giver["telegram_id"]:
                if g["status"] == "created":
                    enqueue_notification(giver["telegram_id"], "gift_opened",
                                         f"🎉 {g['recipient_name']} открыл(а) ваш подарок со сценой из фильма!", conn=conn)
                what = {"scene": "сценой из фильма 🎬", "reaction": "реакцией",
                        "message": "сообщением ✉️"}.get(body.type, "ответом")
                enqueue_notification(giver["telegram_id"], "reply",
                                     f"{body.respondent or 'Получатель'} ответил(а) {what}! Открой таймлайн:",
                                     conn=conn)
        log_consent(gift_id, user["id"] if user else None, "pd_processing", request, conn=conn)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/gifts/{gift_id}/notes")
def add_note(request: Request, gift_id: str, body: NoteCreate):
    user = request_user(request, body.author_telegram_id)
    conn = connect(config.DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        g = conn.execute("SELECT * FROM gifts WHERE id=?", (gift_id,)).fetchone()
        if not g or g["status"] in ("deleted", "expired"):
            raise HTTPException(404, "Подарок не найден")
        if g["open_at"] and parse_iso(g["open_at"]) > now():
            raise HTTPException(403, "Подарок ещё не открыт")
        conn.execute("INSERT INTO gift_notes (gift_id, author_id, text, photo_file_id) VALUES (?,?,?,?)",
                     (gift_id, user["id"] if user else None, body.text, body.photo))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/gifts/{gift_id}/qr.svg")
def gift_qr(request: Request, gift_id: str):
    url = app_url(request, f"/g/{gift_id}")
    return Response(qr_svg(url), media_type="image/svg+xml")


@app.post("/api/gifts/{gift_id}/order-bouquet")
def order_bouquet(request: Request, gift_id: str, body: BouquetOrder):
    """Регистрируем намерение заказать букет и отдаём партнёрскую ссылку агрегатора."""
    conn = connect(config.DB_PATH)
    g = conn.execute("SELECT * FROM gifts WHERE id=?", (gift_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(404, "Подарок не найден")
    b = conn.execute("SELECT * FROM bouquets WHERE id=? AND active=1", (body.bouquet_id,)).fetchone()
    if not b:
        conn.close()
        raise HTTPException(404, "Букет не найден")
    ref = f"scena_{gift_id}_{new_code(4)}"
    amount = body.amount if body.amount is not None else b["price_rub"]
    # если подарок не от студии — привязываем к дефолтной витрине агрегатора
    shop_id = g["shop_id"]
    if not shop_id:
        row = conn.execute("SELECT id FROM shops WHERE name='Flowwow (витрина)'").fetchone()
        if row:
            shop_id = row["id"]
        else:
            cur0 = conn.execute("INSERT INTO shops (name, city, commission_pct) VALUES ('Flowwow (витрина)', 'Россия', ?)",
                                (config.SHOP_COMMISSION_PCT,))
            shop_id = cur0.lastrowid
    cur = conn.execute(
        "INSERT INTO orders (gift_id, shop_id, bouquet_id, ref_code, amount, commission_pct,"
        " commission_rub, status) VALUES (?,?,?,?,?,?,?, 'pending')",
        (gift_id, shop_id, b["id"], ref, amount,
         config.SHOP_COMMISSION_PCT, round(amount * config.SHOP_COMMISSION_PCT / 100, 2)),
    )
    order_id = cur.lastrowid
    conn.execute("UPDATE gifts SET flowwow_ref=? WHERE id=?", (ref, gift_id))
    conn.commit()
    url = order_link(dict(b), gift_id, ref)
    conn.close()
    return {"order_id": order_id, "ref_code": ref, "url": url,
            "go_url": f"{base_url(request)}/api/orders/{order_id}/go",
            "commission_rub": round(amount * config.SHOP_COMMISSION_PCT / 100, 2)}


@app.get("/api/orders/{order_id}/go")
def order_go(order_id: int):
    """Клик по ссылке: фиксируем переход и редиректим на агрегатора."""
    conn = connect(config.DB_PATH)
    o = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        conn.close()
        raise HTTPException(404, "Заказ не найден")
    b = conn.execute("SELECT * FROM bouquets WHERE id=?", (o["bouquet_id"],)).fetchone()
    conn.execute("UPDATE orders SET status='clicked', clicked_at=? WHERE id=? AND status='pending'",
                 (now_iso(), order_id))
    conn.commit()
    conn.close()
    if not b:
        raise HTTPException(404, "Букет не найден")
    return RedirectResponse(order_link(dict(b), o["gift_id"] or "", o["ref_code"] or ""))


# ---------- таймлайн, «ваш фильм», связи ----------
@app.get("/api/timeline/{telegram_id}")
def timeline(telegram_id: int, request: Request):
    require_self(request, telegram_id)
    conn = connect(config.DB_PATH)
    u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    gifts = conn.execute("SELECT * FROM gifts WHERE giver_id=? ORDER BY created_at DESC",
                         (u["id"],)).fetchall()
    items = []
    for g in gifts:
        gd = dict(g)
        gs = conn.execute(
            "SELECT s.movie_title, s.movie_year, s.timecode_label, s.poster_url, gs.day_index"
            " FROM gift_scenes gs JOIN scenes s ON s.id=gs.scene_id WHERE gs.gift_id=?",
            (gd["id"],)).fetchall()
        reps = conn.execute(
            "SELECT r.type, r.reaction, r.message_text, r.created_at, s.movie_title, s.timecode_label"
            " FROM replies r LEFT JOIN scenes s ON s.id=r.scene_id WHERE r.gift_id=? ORDER BY r.created_at",
            (gd["id"],)).fetchall()
        items.append({"gift": gd, "scenes": [dict(x) for x in gs],
                      "replies": [dict(x) for x in reps]})
    peers = conn.execute(
        "SELECT u2.telegram_id, u2.first_name FROM connections c JOIN users u2"
        " ON u2.id = CASE WHEN c.user_a=? THEN c.user_b ELSE c.user_a END WHERE ? IN (c.user_a, c.user_b) AND c.status='accepted'",
        (u["id"], u["id"])).fetchall()
    conn.close()
    return {"user": dict(u), "items": items, "total": len(items),
            "peers": [dict(p) for p in peers]}


@app.get("/api/film/{telegram_id}")
def film_summary(telegram_id: int, request: Request, year: Optional[int] = None):
    require_self(request, telegram_id)
    """«Ваш фильм» года: сводка по таймлайну пользователя."""
    year = year or now().year
    conn = connect(config.DB_PATH)
    u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    gifts = conn.execute(
        "SELECT * FROM gifts WHERE giver_id=? AND strftime('%Y', created_at)=?",
        (u["id"], str(year))).fetchall()
    scene_count = 0
    reply_count = 0
    movies = {}
    first = last = None
    for g in gifts:
        gs = conn.execute(
            "SELECT s.* FROM gift_scenes gs JOIN scenes s ON s.id=gs.scene_id WHERE gs.gift_id=?",
            (g["id"],)).fetchall()
        scene_count += len(gs)
        for s in gs:
            d = row_to_dict(s)
            movies[d["movie_title"]] = movies.get(d["movie_title"], 0) + 1
        reply_count += conn.execute("SELECT COUNT(*) c FROM replies WHERE gift_id=?",
                                    (g["id"],)).fetchone()["c"]
        first = g["created_at"] if not first or g["created_at"] < first else first
        last = g["created_at"] if not last or g["created_at"] > last else last
    conn.close()
    top = sorted(movies.items(), key=lambda kv: -kv[1])[:5]
    return {
        "year": year, "user": dict(u),
        "gifts": len(gifts), "scenes": scene_count, "replies": reply_count,
        "first": first, "last": last, "top_movies": [{"title": t, "count": c} for t, c in top],
        "cover_emoji": "🎬",
        "share_url": app_url(request, f"/film?y={year}"),
    }


@app.get("/api/film/{telegram_id}/export")
def film_export(telegram_id: int, request: Request, year: Optional[int] = None):
    require_self(request, telegram_id)
    """Премиум-экспорт «вашего фильма» — отдельный HTML-файл (печатать в PDF/книгу)."""
    from html import escape
    year = year or now().year
    conn = connect(config.DB_PATH)
    u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    premium = bool(u["premium_until"]) and parse_db(u["premium_until"]) > now()
    if not premium:
        conn.close()
        raise HTTPException(402, "Экспорт доступен с премиумом «ваш фильм» — 499 ₽/год")
    gifts = conn.execute(
        "SELECT * FROM gifts WHERE giver_id=? AND strftime('%Y', created_at)=? ORDER BY created_at",
        (u["id"], str(year))).fetchall()
    parts = []
    total_scenes = 0
    total_replies = 0
    for g in gifts:
        gs = conn.execute(
            "SELECT gs.day_index, s.* FROM gift_scenes gs JOIN scenes s ON s.id=gs.scene_id"
            " WHERE gs.gift_id=? ORDER BY gs.day_index", (g["id"],)).fetchall()
        reps = conn.execute(
            "SELECT r.*, s.movie_title, s.timecode_label FROM replies r"
            " LEFT JOIN scenes s ON s.id=r.scene_id WHERE r.gift_id=? ORDER BY r.created_at",
            (g["id"],)).fetchall()
        notes = conn.execute("SELECT * FROM gift_notes WHERE gift_id=?", (g["id"],)).fetchall()
        total_scenes += len(gs)
        total_replies += len(reps)
        scene_html = "".join(
            f'<div class="scene"><div class="mv">{escape(s["movie_title"])} ({s["movie_year"]})</div>'
            f'<div class="tc">⏱ {escape(s["timecode_label"] or "")}</div>'
            f'<p>{escape(s["description"] or "")}</p>'
            f'<p class="why">{escape(s["why_fits"] or "")}</p></div>' for s in gs)
        reply_html = "".join(
            f'<div class="rep"><b>{escape(r["respondent"] or "Получатель")}</b> · '
            + (f'🎬 {escape(r["movie_title"] or "")} ⏱ {escape(r["timecode_label"] or "")}'
               if r["type"] == "scene"
               else escape(r["reaction"] or r["message_text"] or "")) + '</div>'
            for r in reps)
        note_html = "".join(f'<div class="rep">📝 {escape(n["text"] or "")}</div>' for n in notes)
        msg_html = (f'<p class="msg">💌 {escape(g["message_text"])}</p>'
                    if g["message_text"] else "")
        parts.append(
            f'<section class="gift"><div class="date">{escape(g["created_at"][:10])}</div>'
            f'<h3>🎁 {escape(g["title"] or "Подарок")} — для {escape(g["recipient_name"])}'
            f' <span class="occ">{escape(g["occasion"] or "")}</span></h3>'
            f'{msg_html}{scene_html}{reply_html}{note_html}</section>')
    conn.close()
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Ваш фильм {year} — Сцена</title>
<style>
body{{font-family:Georgia,serif;max-width:720px;margin:40px auto;color:#222;line-height:1.55}}
.cover{{text-align:center;padding:40px;background:linear-gradient(135deg,#7c5cff,#ff5c8a);color:#fff;border-radius:18px}}
.cover .big{{font-size:56px}} .cover h1{{margin:10px 0}}
.stats{{display:flex;gap:18px;justify-content:center;margin:18px 0}}
.gift{{border:1px solid #ddd;border-radius:14px;padding:18px;margin:18px 0}}
.date{{color:#888;font-size:13px}} .occ{{color:#a00;font-size:13px}}
.scene{{background:#f7f5ff;border-left:4px solid #7c5cff;padding:10px 14px;margin:10px 0;border-radius:0 10px 10px 0}}
.mv{{font-weight:bold}} .tc{{color:#ff5c8a;font-weight:bold}} .why{{color:#666;font-style:italic;font-size:13px}}
.rep{{font-size:14px;margin-top:6px}}
.msg{{background:#fff5f7;border-left:4px solid #ff5c8a;padding:8px 12px;border-radius:0 8px 8px 0}}
footer{{text-align:center;color:#999;font-size:12px;margin-top:30px}}
</style></head><body>
<div class="cover"><div class="big">🎬</div><h1>Ваш фильм {year}</h1>
<div>{escape(u["first_name"] or "Вы")}</div></div>
<div class="stats"><div>🎁 {len(gifts)} подарков</div><div>🎞️ {total_scenes} сцен</div><div>💬 {total_replies} ответов</div></div>
{''.join(parts) or '<p>Пока пусто.</p>'}
<footer>Собрано в приложении «Сцена» 🎬 — дарите моменты из фильмов</footer>
</body></html>"""
    return HTMLResponse(html)


from .connections import router as connections_router
app.include_router(connections_router)
from .account import router as account_router
app.include_router(account_router)
from .operations import router as operations_router, audit, worker_health
app.include_router(operations_router)


# ---------- календарь дат ----------
@app.get("/api/dates")
def list_dates(request: Request, telegram_id: Optional[int] = None):
    user = request_user(request, telegram_id)
    if not user:
        return {"dates": []}
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT * FROM important_dates WHERE user_id=? ORDER BY date",
                        (user["id"],)).fetchall()
    conn.close()
    return {"dates": [dict(r) for r in rows]}


@app.get("/api/dates/{telegram_id}/ics")
def dates_ics(telegram_id: int, request: Request):
    require_self(request, telegram_id)
    """Экспорт важных дат в .ics (импорт в любой календарь)."""
    conn = connect(config.DB_PATH)
    u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    rows = conn.execute("SELECT * FROM important_dates WHERE user_id=?", (u["id"],)).fetchall()
    conn.close()
    year = now().year
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Scena//Dates//RU"]
    for r in rows:
        mm, dd = r["date"].split("-")
        d = f"{year}{mm}{dd}"
        lines += ["BEGIN:VEVENT",
                  f"UID:scena-{r['id']}@scena",
                  f"DTSTART;VALUE=DATE:{d}",
                  f"SUMMARY:🎁 {r['name']} — повод для «Сцены»",
                  f"DESCRIPTION:Не забудь подарок со сценой из фильма (приложение «Сцена»)",
                  "RRULE:FREQ=YEARLY",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines), media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=scena-dates.ics"})


@app.post("/api/dates")
def add_date(request: Request, body: DateCreate):
    user = request_user(request, body.telegram_id)
    if not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    try:
        if len(body.date) != 5 or body.date[2] != "-":
            raise ValueError()
        datetime.strptime("2000-" + body.date, "%Y-%m-%d")  # allow leap-day anniversaries
    except ValueError:
        raise HTTPException(400, "Укажите существующую дату в формате MM-DD")
    conn = connect(config.DB_PATH)
    cur = conn.execute(
        "INSERT INTO important_dates (user_id, name, date, remind_days) VALUES (?,?,?,?)",
        (user["id"], body.name, body.date, body.remind_days))
    conn.commit()
    conn.close()
    return {"ok": True, "id": cur.lastrowid}


# ---------- витрина букетов ----------
@app.get("/api/bouquets")
def list_bouquets(city: Optional[str] = None, q: Optional[str] = None, limit: int = 20):
    conn = connect(config.DB_PATH)
    sql = "SELECT * FROM bouquets WHERE active=1"
    args = []
    if city:
        sql += " AND city=?"
        args.append(city)
    if q:
        sql += " AND title LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY price_rub LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return {"bouquets": [dict(r) for r in rows]}


@app.get("/api/cities")
def list_cities():
    """Города, где есть витрина букетов (для автоподбора по городу получателя)."""
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT DISTINCT city FROM bouquets WHERE active=1 ORDER BY city").fetchall()
    conn.close()
    return {"cities": [r["city"] for r in rows]}


# ---------- студии и QR-карточки (B2B) ----------
@app.post("/api/shops")
def create_shop(request: Request, body: ShopCreate):
    # prod: регистрация студии только по админ-токену (или заявкой — см. docs/05);
    # в dev/anon-режиме разрешаем свободно (для демо)
    if not config.ALLOW_ANON:
        require_admin(request)
    conn = connect(config.DB_PATH)
    cur = conn.execute(
        "INSERT INTO shops (name, city, contact, email, flowwow_direct_link, commission_pct)"
        " VALUES (?,?,?,?,?,?)",
        (body.name, body.city, body.contact, body.email, body.flowwow_direct_link,
         config.SHOP_COMMISSION_PCT))
    conn.commit()
    shop_id = cur.lastrowid
    conn.close()
    return {"shop_id": shop_id}


@app.post("/api/shops/{shop_id}/cards")
def order_cards(request: Request, shop_id: int, body: CardsOrder):
    if not config.ALLOW_ANON:
        require_admin(request)
    conn = connect(config.DB_PATH)
    if not conn.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Магазин не найден")
    codes = []
    for _ in range(body.count):
        code = new_code(8)
        conn.execute("INSERT INTO cards (code, shop_id, printed_at) VALUES (?,?,?)",
                     (code, shop_id, now_iso()))
        codes.append(code)
    conn.execute("UPDATE shops SET card_balance = card_balance + ? WHERE id=?", (body.count, shop_id))
    audit(conn, request, "cards_issued_by_operator", shop_id, {"count": body.count})
    conn.commit()
    conn.close()
    return {"shop_id": shop_id, "count": body.count, "codes": codes,
            "urls": [app_url(request, f"/card/{c}") for c in codes[:5]]}


@app.get("/api/cards/{code}")
def card_info(code: str):
    _c = connect(config.DB_PATH)
    track_event(_c, "card_scan", code, None, {})
    _c.commit(); _c.close()
    conn = connect(config.DB_PATH)
    c = conn.execute(
        "SELECT c.*, s.name AS shop_name, s.city AS shop_city, g.recipient_name, g.id AS gift_code"
        " FROM cards c JOIN shops s ON s.id=c.shop_id LEFT JOIN gifts g ON g.id=c.gift_id"
        " WHERE c.code=?", (code,)).fetchone()
    conn.close()
    if not c:
        raise HTTPException(404, "Карточка не найдена")
    return dict(c)


@app.get("/api/cards/{code}/qr.svg")
def card_qr(request: Request, code: str):
    return Response(qr_svg(app_url(request, f"/card/{code}")), media_type="image/svg+xml")


@app.get("/api/cards/{code}/preview", response_class=HTMLResponse)
def card_preview(request: Request, code: str):
    """Печатный макет QR-карточки (A6, 105×148 мм) для типографии."""
    from html import escape
    conn = connect(config.DB_PATH)
    c = conn.execute("SELECT c.*, s.name AS shop_name, s.city AS shop_city FROM cards c"
                     " JOIN shops s ON s.id=c.shop_id WHERE c.code=?", (code,)).fetchone()
    conn.close()
    if not c:
        raise HTTPException(404, "Карточка не найдена")
    url = app_url(request, f"/card/{code}")
    svg = qr_svg(url)
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Карточка «Сцена»</title>
<style>
@page {{ size: 105mm 148mm; margin: 0; }}
body {{ margin:0; font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; }}
.card {{ width:105mm; height:148mm; box-sizing:border-box; padding:10mm; display:flex; flex-direction:column;
  align-items:center; justify-content:space-between; background:linear-gradient(160deg,#241f36,#0d0b14);
  color:#f3f0fa; border-radius:6mm; }}
.logo {{ font-size:11mm; font-weight:800; background:linear-gradient(90deg,#ff5c8a,#7c5cff);
  -webkit-background-clip:text; background-clip:text; color:transparent; }}
.qr {{ background:#fff; padding:4mm; border-radius:4mm; width:52mm; height:52mm; }}
.qr svg {{ width:100%; height:100%; }}
.hint {{ font-size:4.2mm; color:#a79fc0; text-align:center; line-height:1.5; }}
.shop {{ font-size:3.8mm; color:#a79fc0; }}
</style></head><body>
<div class="card">
  <div class="logo">🎬 Сцена</div>
  <div class="qr">{svg}</div>
  <div class="hint">Отсканируй QR — и подари близкому<br><b>сцену из фильма с таймкодом</b> 🎁<br>и получи ответ своей сценой</div>
  <div class="shop">{escape(c['shop_name'] or '')} · {escape(c['shop_city'] or '')}</div>
</div>
</body></html>"""


@app.post("/api/orders")
def register_order(request: Request, body: OrderRegister):
    require_shop(request, body.shop_id)
    conn = connect(config.DB_PATH)
    shop = conn.execute("SELECT * FROM shops WHERE id=?", (body.shop_id,)).fetchone()
    if not shop:
        conn.close()
        raise HTTPException(404, "Магазин не найден")
    pct = shop["commission_pct"]
    conn.execute(
        "INSERT INTO orders (gift_id, shop_id, flowwow_order_id, amount, commission_pct,"
        " commission_rub, status) VALUES (?,?,?,?,?,?,'pending')",
        (body.gift_id, body.shop_id, body.flowwow_order_id, body.amount, pct,
         round(body.amount * pct / 100, 2)))
    conn.commit()
    conn.close()
    return {"ok": True, "commission_rub": round(body.amount * pct / 100, 2), "commission_pct": pct}


class OrderConfirm(BaseModel):
    flowwow_order_id: str
    amount: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)   # если не передана — берём из нашего заказа по ref/id
    ref_code: Optional[str] = None


@app.post("/api/shops/{shop_id}/orders/confirm")
def shop_confirm_order(request: Request, shop_id: int, body: OrderConfirm):
    """Агентский контур: студия подтверждает заказ Flowwow по id/ref — начисляем комиссию."""
    require_shop(request, shop_id)
    conn = connect(config.DB_PATH)
    if not conn.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Магазин не найден")
    o = None
    if body.ref_code:
        o = conn.execute("SELECT * FROM orders WHERE shop_id=? AND ref_code=?", (shop_id, body.ref_code)).fetchone()
    if not o:
        o = conn.execute("SELECT * FROM orders WHERE shop_id=? AND flowwow_order_id=?",
                         (shop_id, body.flowwow_order_id)).fetchone()
    if not o:
        # создаём запись заказа задним числом (если перешли на Flowwow без нашего order)
        amount = body.amount or 0
        cur = conn.execute(
            "INSERT INTO orders (shop_id, flowwow_order_id, ref_code, amount, commission_pct,"
            " commission_rub, status) VALUES (?,?,?,?,?,?, 'confirmed')",
            (shop_id, body.flowwow_order_id, body.ref_code, amount,
             config.SHOP_COMMISSION_PCT, round(amount * config.SHOP_COMMISSION_PCT / 100, 2)))
        audit(conn, request, "shop_order_confirmed", shop_id, {"order_id": cur.lastrowid})
        conn.commit()
        oid = cur.lastrowid
        conn.close()
        return {"ok": True, "order_id": oid, "status": "confirmed",
                "commission_rub": round(amount * config.SHOP_COMMISSION_PCT / 100, 2)}
    o = dict(o)
    amount = body.amount if body.amount is not None else o["amount"]
    conn.execute(
        "UPDATE orders SET status='confirmed', flowwow_order_id=COALESCE(?, flowwow_order_id),"
        " amount=?, commission_rub=? WHERE id=?",
        (body.flowwow_order_id, amount, round(amount * o["commission_pct"] / 100, 2), o["id"]))
    audit(conn, request, "shop_order_confirmed", shop_id, {"order_id": o["id"]})
    conn.commit()
    conn.close()
    return {"ok": True, "order_id": o["id"], "status": "confirmed",
            "commission_rub": round(amount * o["commission_pct"] / 100, 2)}


@app.get("/api/shops/{shop_id}/orders")
def shop_orders(request: Request, shop_id: int, status: Optional[str] = None):
    """Список заказов студии (для реестра агентского вознаграждения)."""
    require_shop(request, shop_id)
    conn = connect(config.DB_PATH)
    sql = "SELECT * FROM orders WHERE shop_id=?"
    args = [shop_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    rows = conn.execute(sql + " ORDER BY created_at DESC", args).fetchall()
    conn.close()
    return {"orders": [dict(r) for r in rows]}


@app.get("/api/shops/{shop_id}/stats")
def shop_stats(shop_id: int, request: Request):
    require_shop(request, shop_id)
    conn = connect(config.DB_PATH)
    shop = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    if not shop:
        conn.close()
        raise HTTPException(404, "Магазин не найден")
    stats = {
        "cards_total": conn.execute("SELECT COUNT(*) c FROM cards WHERE shop_id=?", (shop_id,)).fetchone()["c"],
        "cards_used": conn.execute("SELECT COUNT(*) c FROM cards WHERE shop_id=? AND status='used'", (shop_id,)).fetchone()["c"],
        "gifts_created": conn.execute("SELECT COUNT(*) c FROM gifts WHERE shop_id=?", (shop_id,)).fetchone()["c"],
    }
    rev = conn.execute(
        "SELECT COALESCE(SUM(commission_rub),0) s, COUNT(*) c FROM orders"
        " WHERE shop_id=? AND status<>'cancelled'", (shop_id,)).fetchone()
    conn.close()
    return {"shop": dict(shop), **stats, "commission_rub": rev["s"], "orders": rev["c"]}


# ---------- платежи (ЮKassa / dev) ----------
class PaymentCreate(BaseModel):
    kind: str                       # card_pack / subscription / premium_film
    count: int = Field(default=0, ge=0, le=10000)  # для card_pack
    shop_id: Optional[int] = None
    telegram_id: Optional[int] = None


@app.post("/api/payments/create")
async def create_payment(request: Request, body: PaymentCreate):
    if not config.ALLOW_ANON and not (config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY):
        raise HTTPException(503, "Оплата временно недоступна")
    if body.kind not in ("card_pack", "subscription", "premium_film"):
        raise HTTPException(400, "kind: card_pack | subscription | premium_film")
    if body.kind == "card_pack" and body.count < 1:
        raise HTTPException(400, "Количество карточек должно быть от 1 до 10000")
    if body.kind == "premium_film" and body.shop_id is not None:
        raise HTTPException(400, "Для премиума не указывается студия")
    user = request_user(request, body.telegram_id)
    if body.kind == "premium_film" and not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    if body.kind in ("card_pack", "subscription"):
        shop_id = body.shop_id
        if not shop_id and user:
            # владелец студии? берём первую его студию (упрощённо — задаётся явно)
            pass
        if not shop_id:
            raise HTTPException(400, "для card_pack/subscription нужен shop_id")
        require_shop(request, shop_id)
        conn = connect(config.DB_PATH)
        try:
            if not conn.execute("SELECT 1 FROM shops WHERE id=?", (shop_id,)).fetchone():
                raise HTTPException(404, "Магазин не найден")
        finally:
            conn.close()
    p = await payments.create_payment(
        body.kind, user_id=user["id"] if user else None, shop_id=body.shop_id,
        count=body.count, base_url=config.PUBLIC_URL.rstrip("/") if not config.ALLOW_ANON else base_url(request))
    return p


@app.get("/api/payments/{pid}")
def payment_status(pid: str, request: Request):
    p = payments.get_payment(pid)
    if not p:
        raise HTTPException(404, "Платёж не найден")
    if not config.ALLOW_ANON:
        if p["shop_id"]:
            require_shop(request, p["shop_id"])
        else:
            user = request_user(request)
            if not user or user["id"] != p["user_id"]:
                require_admin(request)
    return p


@app.post("/api/payments/{pid}/confirm-dev")
def payment_confirm_dev(pid: str):
    """Dev-режим: подтвердить оплату без ЮKassa (в prod не используется)."""
    if config.YUKASSA_SHOP_ID or not config.ALLOW_ANON:
        raise HTTPException(400, "В prod оплата идёт через ЮKassa")
    p = payments.confirm_dev(pid)
    if not p:
        raise HTTPException(404, "Платёж не найден")
    return p


@app.post("/api/payments/webhook", include_in_schema=False)
async def payments_webhook(request: Request):
    event = await request.json()
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid event")
    if config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY:
        event = await payments.verify_provider_event(event)
    elif not config.ALLOW_ANON:
        raise HTTPException(503, "Payments are not configured")
    await run_in_threadpool(payments.handle_provider_webhook, event)
    return {"ok": True}



@app.post("/api/shops/{shop_id}/buy-cards")
async def shop_buy_cards(request: Request, shop_id: int, body: CardsOrder):
    require_shop(request, shop_id)
    if not config.ALLOW_ANON and not (config.YUKASSA_SHOP_ID and config.YUKASSA_SECRET_KEY):
        raise HTTPException(503, "Оплата временно недоступна")
    """Студия покупает пачку карточек — создаём платёж card_pack."""
    conn = connect(config.DB_PATH)
    if not conn.execute("SELECT id FROM shops WHERE id=?", (shop_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Магазин не найден")
    conn.close()
    return await payments.create_payment("card_pack", shop_id=shop_id, count=body.count,
                                         base_url=config.PUBLIC_URL.rstrip("/") if not config.ALLOW_ANON else base_url(request))


class ShopOwnerAssignment(BaseModel):
    telegram_id: int


@app.post("/api/admin/shops/{shop_id}/owner")
def assign_shop_owner(shop_id: int, request: Request, body: ShopOwnerAssignment):
    require_admin(request)
    conn = connect(config.DB_PATH)
    try:
        user = conn.execute("SELECT id FROM users WHERE telegram_id=?", (body.telegram_id,)).fetchone()
        if not user:
            raise HTTPException(404, "Владелец должен сначала войти в Mini App")
        cur = conn.execute("UPDATE shops SET owner_user_id=? WHERE id=?", (user["id"], shop_id))
        if not cur.rowcount:
            raise HTTPException(404, "Студия не найдена")
        audit(conn, request, "shop_owner_assigned", shop_id)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/shops/mine")
def my_shops(request: Request, telegram_id: Optional[int] = None):
    user = request_user(request, telegram_id)
    if not user:
        raise HTTPException(401, "Нужен Telegram-аккаунт")
    conn = connect(config.DB_PATH)
    try:
        rows = conn.execute("SELECT id,name,city FROM shops WHERE owner_user_id=?", (user["id"],)).fetchall()
        return {"shops": [dict(row) for row in rows]}
    finally:
        conn.close()


# ---------- админка ----------
@app.get("/api/admin/analytics")
def admin_analytics(request: Request):
    """Воронка: события по kind + по дням за 30 дней."""
    require_admin(request)
    conn = connect(config.DB_PATH)
    by_kind = conn.execute(
        "SELECT kind, COUNT(*) c FROM events GROUP BY kind ORDER BY c DESC").fetchall()
    by_day = conn.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*) c FROM events"
        " WHERE created_at >= datetime('now','-30 days') GROUP BY d ORDER BY d").fetchall()
    funnel = {k: 0 for k in ("card_scan", "gift_created", "gift_opened", "gift_replied")}
    for r in by_kind:
        if r["kind"] in funnel:
            funnel[r["kind"]] = r["c"]
    conn.close()
    return {"by_kind": [dict(r) for r in by_kind], "by_day": [dict(r) for r in by_day], "funnel": funnel}


@app.get("/api/users/{telegram_id}/export")
def user_data_export(request: Request, telegram_id: int):
    """Экспорт данных пользователя по 152-ФЗ (ст. 14): сам пользователь (initData) или админ."""
    user = request_user(request, telegram_id)
    if not config.ALLOW_ANON:
        if not user or user["telegram_id"] != telegram_id:
            require_admin(request)
            user = None
    conn = connect(config.DB_PATH)
    u = conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    uid = u["id"]
    gifts = conn.execute("SELECT * FROM gifts WHERE giver_id=?", (uid,)).fetchall()
    gift_ids = [g["id"] for g in gifts]
    replies = conn.execute("SELECT * FROM replies WHERE gift_id IN (%s)" % (",".join("?" * len(gift_ids)) or "''"),
                           gift_ids).fetchall() if gift_ids else []
    notes = conn.execute("SELECT * FROM gift_notes WHERE gift_id IN (%s)" % (",".join("?" * len(gift_ids)) or "''"),
                         gift_ids).fetchall() if gift_ids else []
    dates = conn.execute("SELECT * FROM important_dates WHERE user_id=?", (uid,)).fetchall()
    bookmarks = conn.execute("SELECT * FROM bookmarks WHERE user_id=?", (uid,)).fetchall()
    consents = conn.execute("SELECT * FROM consents WHERE user_id=?", (uid,)).fetchall()
    import hashlib
    actor_subject = 'tg-sha256:' + hashlib.sha256(str(telegram_id).encode()).hexdigest()
    activity = {
        "audit_events": [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE actor=?", (actor_subject,))],
        "owned_shops": [dict(r) for r in conn.execute("SELECT * FROM shops WHERE owner_user_id=?", (uid,))],
        "suggested_scenes": [dict(r) for r in conn.execute("SELECT * FROM scenes WHERE suggested_by=?", (uid,))],
        "scene_lists": [dict(r) for r in conn.execute("SELECT * FROM scene_lists WHERE user_id=?", (uid,))],
        "list_scenes": [dict(r) for r in conn.execute("SELECT ls.* FROM list_scenes ls JOIN scene_lists l ON l.id=ls.list_id WHERE l.user_id=?", (uid,))],
        "my_reports": [dict(r) for r in conn.execute("SELECT * FROM gift_reports WHERE reporter_telegram_id=?", (telegram_id,))],
        "my_replies": [dict(r) for r in conn.execute("SELECT * FROM replies WHERE respondent_user_id=?", (uid,))],
        "my_notes": [dict(r) for r in conn.execute("SELECT * FROM gift_notes WHERE author_id=?", (uid,))],
        "payments": [dict(r) for r in conn.execute("SELECT id,kind,amount_rub,status,created_at,paid_at FROM payments WHERE user_id=?", (uid,))],
        "notifications": [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE telegram_id=?", (telegram_id,))],
        "events": [dict(r) for r in conn.execute("SELECT * FROM events WHERE telegram_id=?", (telegram_id,))],
        "connections": [dict(r) for r in conn.execute("SELECT * FROM connections WHERE user_a=? OR user_b=?", (uid, uid))],
    }
    conn.close()
    return {
        "activity": activity,
        "exported_at": now_iso(), "user": dict(u),
        "gifts": [dict(g) for g in gifts],
        "replies": [dict(r) for r in replies],
        "notes": [dict(n) for n in notes],
        "dates": [dict(d) for d in dates],
        "bookmarks": [dict(b) for b in bookmarks],
        "consents": [dict(c) for c in consents],
    }


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    require_admin(request)
    conn = connect(config.DB_PATH)
    out = {}
    for t in ("users", "scenes", "gifts", "replies", "shops", "cards", "orders", "notifications"):
        out[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    out["scenes_draft"] = conn.execute("SELECT COUNT(*) c FROM scenes WHERE status='draft'").fetchone()["c"]
    out["revenue_rub"] = conn.execute(
        "SELECT COALESCE(SUM(commission_rub),0) s FROM orders WHERE status IN ('confirmed','paid')"
    ).fetchone()["s"]
    conn.close()
    return out


class SceneVerify(BaseModel):
    service: Optional[str] = None        # kinopoisk / ivi / okko / youtube
    version_note: Optional[str] = None   # версия издания, для которой верен таймкод
    timecode_label: Optional[str] = None
    timecode_seconds: Optional[int] = None
    timecode_end_label: Optional[str] = None   # конец отрезка сцены
    timecode_end_seconds: Optional[int] = None
    watch_url: Optional[str] = None      # прямой deep link в плеер с таймкодом


class TimecodeReview(BaseModel):
    action: str  # approve / reject
    service: Optional[str] = None
    timecode_label: Optional[str] = None
    timecode_end_label: Optional[str] = None   # конец отрезка сцены
    version_note: Optional[str] = None
    watch_url: Optional[str] = None
    moment_label: Optional[str] = None  # короткая метка НАШИМИ словами: что в этом моменте (без реплик)


@app.get("/api/admin/timecodes")
def admin_timecodes(request: Request, status: str = "pending"):
    """Очередь кандидатов таймкодов из внешних библиотек (OpenSubtitles/OPUS и др.)."""
    require_admin(request)
    conn = connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT tc.*, s.movie_title, s.movie_year, s.genre, s.description, s.why_fits"
        " FROM timecode_candidates tc JOIN scenes s ON s.id=tc.scene_id"
        " WHERE tc.status=? ORDER BY tc.created_at", (status,)).fetchall()
    conn.close()
    return {"candidates": [dict(r) for r in rows]}


class BulkApprove(BaseModel):
    min_confidence: float = 0.8
    min_consensus: int = 2
    only_auto: bool = True


@app.post("/api/admin/timecodes/bulk-approve")
def admin_timecodes_bulk_approve(request: Request, body: BulkApprove):
    """Авто-одобрение консенсусных кандидатов: несколько независимых версий субтитров сошлись
    на одном моменте (±10 сек) и высокая уверенность → verified=1 без ручного клика.
    Все остальные остаются в ручной очереди. Текст субтитров по-прежнему нигде не хранится."""
    require_admin(request)
    conn = connect(config.DB_PATH)
    sql = ("SELECT * FROM timecode_candidates WHERE status='pending' AND consensus>=? AND confidence>=?")
    args = [body.min_consensus, body.min_confidence]
    if body.only_auto:
        sql += " AND auto=1"
    rows = conn.execute(sql, args).fetchall()
    n = 0
    for c in rows:
        conn.execute(
            "UPDATE scenes SET timecode_label=?, timecode_seconds=?, timecode_end_label=?, timecode_end_seconds=?,"
            " service=?, verified=1, version_note=? WHERE id=?",
            (c["timecode_label"], c["timecode_seconds"], c["timecode_end_label"], c["timecode_end_seconds"],
             c["service"],
             f"auto-consensus({c['consensus']} версии субтитров), {c['source']}", c["scene_id"]))
        conn.execute("UPDATE timecode_candidates SET status='approved', reviewed_at=?, reviewed_by='auto-consensus' WHERE id=?",
                     (now_iso(), c["id"]))
        n += 1
    conn.commit()
    conn.close()
    return {"ok": True, "approved": n}


@app.get("/api/scenes/{scene_id}/moments")
def scene_moments(scene_id: int):
    """Одобренные моменты (таймкоды) сцены — клиент выбирает, какой момент дарить."""
    conn = connect(config.DB_PATH)
    s = conn.execute("SELECT id, movie_title, movie_year, timecode_label, timecode_end_label, verified FROM scenes WHERE id=?",
                     (scene_id,)).fetchone()
    if not s or s["id"] is None:
        conn.close()
        raise HTTPException(404, "Сцена не найдена")
    cands = conn.execute(
        "SELECT id, timecode_label, timecode_seconds, timecode_end_label, timecode_end_seconds,"
        " service, moment_label, confidence, consensus"
        " FROM timecode_candidates WHERE scene_id=? AND status='approved' ORDER BY confidence DESC",
        (scene_id,)).fetchall()
    conn.close()
    moments = [dict(c) for c in cands]
    if s["timecode_label"] and not any(m["timecode_label"] == s["timecode_label"] for m in moments):
        moments.insert(0, {"id": 0, "timecode_label": s["timecode_label"],
                           "timecode_end_label": s["timecode_end_label"],
                           "timecode_seconds": None, "timecode_end_seconds": None,
                           "service": None,
                           "moment_label": "основной таймкод сцены",
                           "confidence": 1.0, "consensus": None})
    return {"scene": dict(s), "moments": moments}


@app.post("/api/admin/timecodes/{cand_id}/review")
def admin_timecode_review(request: Request, cand_id: int, body: TimecodeReview):
    """Approve: таймкод становится официальным у сцены (verified=1, service+version_note).
    Reject: кандидат отклонён. Текст субтитров нигде не хранится — сверка вручную в плеере."""
    require_admin(request)
    conn = connect(config.DB_PATH)
    c = conn.execute("SELECT * FROM timecode_candidates WHERE id=?", (cand_id,)).fetchone()
    if not c:
        conn.close()
        raise HTTPException(404, "Кандидат не найден")
    if body.action == "approve":
        label = body.timecode_label or c["timecode_label"]
        end_label = body.timecode_end_label or c["timecode_end_label"]
        if tc_label_to_seconds(label) is None or (end_label and tc_label_to_seconds(end_label) is None):
            conn.close()
            raise HTTPException(400, "Некорректный таймкод")
        conn.execute(
            "UPDATE scenes SET timecode_label=?, timecode_seconds=?, timecode_end_label=?, timecode_end_seconds=?,"
            " service=?, verified=1, version_note=?, watch_url=COALESCE(?, watch_url) WHERE id=?",
            (label, tc_label_to_seconds(label), end_label, tc_label_to_seconds(end_label) if end_label else None,
             body.service or c["service"],
             body.version_note or f"проверено по субтитрам {c['source']}", body.watch_url,
             c["scene_id"]))
        conn.execute("UPDATE timecode_candidates SET status='approved', reviewed_at=?, reviewed_by='admin', "
                     "timecode_label=?, timecode_seconds=?, timecode_end_label=?, timecode_end_seconds=?, "
                     "service=?, moment_label=COALESCE(?,moment_label) WHERE id=?",
                     (now_iso(), label, tc_label_to_seconds(label), end_label,
                      tc_label_to_seconds(end_label) if end_label else None, body.service or c["service"],
                      body.moment_label, cand_id))
    elif body.action == "reject":
        conn.execute("UPDATE timecode_candidates SET status='rejected', reviewed_at=?, reviewed_by='admin' WHERE id=?",
                     (now_iso(), cand_id))
    else:
        conn.close()
        raise HTTPException(400, "action должен быть approve/reject")
    conn.commit()
    conn.close()
    return {"ok": True, "id": cand_id, "action": body.action}


@app.get("/api/admin/scenes")
def admin_scenes(request: Request, status: str = "draft", verified: Optional[int] = None):
    require_admin(request)
    conn = connect(config.DB_PATH)
    sql = "SELECT * FROM scenes WHERE status=?"
    args = [status]
    if verified is not None:
        sql += " AND verified=?"
        args.append(verified)
    rows = conn.execute(sql + " ORDER BY created_at DESC", args).fetchall()
    conn.close()
    return {"scenes": [row_to_dict(r) for r in rows]}


@app.post("/api/admin/scenes/{scene_id}/verify")
def admin_verify_scene(request: Request, scene_id: int, body: SceneVerify):
    """Отметить таймкод проверенным вручную (и одобрить сцену)."""
    require_admin(request)
    conn = connect(config.DB_PATH)
    row = conn.execute("SELECT id FROM scenes WHERE id=?", (scene_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Сцена не найдена")
    conn.execute(
        "UPDATE scenes SET service=COALESCE(?, service), version_note=COALESCE(?, version_note),"
        " timecode_label=COALESCE(?, timecode_label), timecode_seconds=COALESCE(?, timecode_seconds),"
        " timecode_end_label=COALESCE(?, timecode_end_label), timecode_end_seconds=COALESCE(?, timecode_end_seconds),"
        " watch_url=COALESCE(?, watch_url), verified=1, verified_at=?, status='approved' WHERE id=?",
        (body.service, body.version_note, body.timecode_label, body.timecode_seconds,
         body.timecode_end_label, body.timecode_end_seconds, body.watch_url, now_iso(), scene_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "scene_id": scene_id, "verified": True}


@app.post("/api/admin/scenes/{scene_id}/status")
def admin_set_scene_status(request: Request, scene_id: int, body: SceneStatus):
    require_admin(request)
    if body.status not in ("draft", "approved", "rejected"):
        raise HTTPException(400, "status: draft | approved | rejected")
    conn = connect(config.DB_PATH)
    # approve НЕ ставит verified автоматически — таймкод сверяется отдельно через /verify
    conn.execute("UPDATE scenes SET status=? WHERE id=?", (body.status, scene_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/admin/shops/{shop_id}/subscribe")
def admin_subscribe(request: Request, shop_id: int, days: int = 30):
    require_admin(request)
    conn = connect(config.DB_PATH)
    until = (now() + timedelta(days=days)).isoformat()
    conn.execute("UPDATE shops SET subscription='active', subscribed_until=? WHERE id=?",
                 (until, shop_id))
    conn.commit()
    conn.close()
    return {"ok": True, "subscribed_until": until}


# ---------- статика ----------
app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="miniapp")
@app.get("/media/{name}", include_in_schema=False)
def download_media(name: str, gift: str = "", expires: int = 0, sig: str = ""):
    if not verify_media(name, gift, expires, sig):
        raise HTTPException(403, "Ссылка на медиа недействительна или истекла")
    conn = connect(config.DB_PATH)
    try:
        g = conn.execute("SELECT * FROM gifts WHERE id=?", (gift,)).fetchone()
        if not g or g["status"] in ("deleted", "expired"):
            raise HTTPException(404, "Медиа недоступно")
        if g["open_at"] and parse_iso(g["open_at"]) > now():
            raise HTTPException(403, "Подарок ещё не открыт")
        path = f"/media/{name}"
        attached = path in (g["message_photo_file_id"], g["message_voice_file_id"])
        if not attached:
            attached = conn.execute(
                "SELECT 1 FROM replies WHERE gift_id=? AND (media_path=? OR photo_file_id=? OR voice_file_id=?)",
                (gift, path, path, path)).fetchone()
        if not attached:
            raise HTTPException(404, "Медиа недоступно")
    finally:
        conn.close()
    # Resolve to prevent symlinks inside media/ escaping the storage root.
    root = os.path.realpath(config.MEDIA_DIR)
    target = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(target) != root or not os.path.isfile(target):
        raise HTTPException(404, "Файл не найден")
    return FileResponse(target, headers={"Cache-Control": "private, no-store",
                                        "Referrer-Policy": "no-referrer",
                                        "X-Content-Type-Options": "nosniff"})
