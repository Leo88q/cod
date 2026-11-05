"""Смоук-тесты API «Сцены» (FastAPI TestClient, изолированная БД)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_scene_search_total(client):
    r = client.post("/api/scenes/search", json={"limit": 50})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 60
    assert data["scenes"][0]["watch_links"]


def test_scene_search_by_occasion(client):
    r = client.post("/api/scenes/search", json={"occasion": "признание", "limit": 10})
    scenes = r.json()["scenes"]
    assert scenes and all("признание" in s["occasion_tags"] for s in scenes)


def _create_gift(client, **over):
    body = {"giver_telegram_id": 111222333, "recipient_name": "Аня",
            "occasion": "годовщина", "tone": "романтичный", "scene_ids": [12],
            "message_text": "С днём!"}
    body.update(over)
    r = client.post("/api/gifts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["gift_id"]


def test_gift_create_and_view(client):
    gid = _create_gift(client)
    r = client.get(f"/api/gifts/{gid}")
    assert r.status_code == 200
    d = r.json()
    assert d["locked"] is False
    assert d["gift"]["status"] == "opened"
    assert d["scenes"][0]["movie_title"]
    assert d["scenes"][0]["watch_links"][0]["url"].startswith("http")


def test_gift_series_locks_days(client):
    gid = _create_gift(client, format="series", series_days=3, scene_ids=[12, 13, 16])
    d = client.get(f"/api/gifts/{gid}").json()
    assert len(d["scenes"]) == 1 and d["locked_count"] == 2


def test_gift_open_at_locked(client):
    gid = _create_gift(client, open_at="2999-01-01T00:00:00+00:00")
    d = client.get(f"/api/gifts/{gid}").json()
    assert d["locked"] is True and d["open_at"].startswith("2999")


def test_reply_and_timeline_and_notification(client):
    gid = _create_gift(client)
    r = client.post(f"/api/gifts/{gid}/reply", json={
        "respondent": "Аня", "respondent_telegram_id": 444555666,
        "type": "scene", "scene_id": 14, "message_text": "💫"})
    assert r.status_code == 200
    d = client.get(f"/api/gifts/{gid}").json()
    assert d["gift"]["status"] == "replied"
    assert d["replies"][0]["movie_title"]
    tl = client.get("/api/timeline/111222333").json()
    assert tl["total"] >= 1
    ov = client.get("/api/admin/overview", headers={"X-Admin-Token": "test-admin"}).json()
    assert ov["notifications"] >= 1  # уведомление дарителю в очереди


def test_gift_qr_svg(client):
    gid = _create_gift(client)
    r = client.get(f"/api/gifts/{gid}/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text


def test_suggest_and_admin_moderation(client):
    r = client.post("/api/scenes/suggest", json={
        "movie_title": "Тест", "movie_year": 2020, "timecode_label": "0:01:00",
        "occasion_tags": ["извинение"], "description": "На модерацию", "telegram_id": 111})
    sid = r.json()["scene_id"]
    # без токена — 403
    assert client.get("/api/admin/scenes?status=draft").status_code == 403
    drafts = client.get("/api/admin/scenes?status=draft",
                        headers={"X-Admin-Token": "test-admin"}).json()["scenes"]
    assert any(s["id"] == sid for s in drafts)
    r = client.post(f"/api/admin/scenes/{sid}/status", json={"status": "approved"},
                    headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    s = client.get(f"/api/scenes/{sid}").json()
    assert s["movie_title"] == "Тест"


def test_shop_cards_payment_flow(client):
    shop = client.post("/api/shops", json={"name": "Студия", "city": "Москва"}).json()["shop_id"]
    # бесплатный выпуск (dev)
    r = client.post(f"/api/shops/{shop}/cards", json={"count": 3})
    assert r.status_code == 200 and len(r.json()["codes"]) == 3
    # покупка с оплатой: платёж pending -> confirm-dev -> paid, баланс растёт
    p = client.post(f"/api/shops/{shop}/buy-cards", json={"count": 10}).json()
    assert p["status"] == "pending" and p["amount_rub"] == 10 * 50
    assert client.get(f"/api/payments/{p['payment_id']}").json()["status"] == "pending"
    paid = client.post(f"/api/payments/{p['payment_id']}/confirm-dev").json()
    assert paid["status"] == "paid" and paid["applied_now"] is True
    # идемпотентность
    again = client.post(f"/api/payments/{p['payment_id']}/confirm-dev").json()
    assert again["applied_now"] is False
    stats = client.get(f"/api/shops/{shop}/stats").json()
    assert stats["cards_total"] == 13 and stats["shop"]["card_balance"] == 13
    # карточка: qr + инфо
    code = client.post(f"/api/shops/{shop}/cards", json={"count": 1}).json()["codes"][0]
    assert client.get(f"/api/cards/{code}/qr.svg").status_code == 200
    assert client.get(f"/api/cards/{code}").json()["shop_name"] == "Студия"


def test_card_gift_flow(client):
    shop = client.post("/api/shops", json={"name": "Студия 2", "city": "СПб"}).json()["shop_id"]
    code = client.post(f"/api/shops/{shop}/cards", json={"count": 1}).json()["codes"][0]
    gid = _create_gift(client, card_code=code)
    card = client.get(f"/api/cards/{code}").json()
    assert card["status"] == "used" and card["gift_code"] == gid
    # повторное использование — 409
    r = client.post("/api/gifts", json={"recipient_name": "X", "scene_ids": [1], "card_code": code})
    assert r.status_code == 409


def test_bouquet_order_flow(client):
    gid = _create_gift(client)
    bq = client.get("/api/bouquets", params={"city": "Москва"}).json()["bouquets"][0]
    o = client.post(f"/api/gifts/{gid}/order-bouquet",
                    json={"bouquet_id": bq["id"], "amount": bq["price_rub"]}).json()
    assert "ref=scena_" in o["url"] and "utm_source=scena" in o["url"]
    assert o["commission_rub"] == round(bq["price_rub"] * 10 / 100, 2)
    r = client.get(f"/api/orders/{o['order_id']}/go", follow_redirects=False)
    assert r.status_code in (302, 307) and "flowwow.com" in r.headers["location"]


def test_dates_and_film(client):
    r = client.post("/api/dates", json={"name": "Мама", "date": "03-08", "remind_days": 7,
                                        "telegram_id": 111222333})
    assert r.status_code == 200
    dates = client.get("/api/dates", params={"telegram_id": 111222333}).json()["dates"]
    assert any(d["name"] == "Мама" for d in dates)
    f = client.get("/api/film/111222333").json()
    assert f["gifts"] >= 1 and f["scenes"] >= 1


def test_upload(client):
    r = client.post("/api/upload", files={"file": ("p.png", b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02\x08\x02\x00\x00\x00\xfd\xd4\x9as\x00\x00\x00\x16IDATx\x9cc\xfc\xcf\xc0\xc0\xc0\xc0\xc0\xc4\xc0\xc0\xc0\xc0\xc0\x00\x00\r\x1d\x01\x03j\xc2\x9b\xe9\x00\x00\x00\x00IEND\xaeB`\x82', "image/png")})
    assert r.status_code == 200 and r.json()["path"].startswith("/media/")
    bad = client.post("/api/upload", files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 400


def test_static_miniapp(client):
    assert client.get("/app/").status_code == 200
    assert client.get("/app/app.js").status_code == 200


def test_landing(client):
    r = client.get("/")
    assert r.status_code == 200 and "Сцена" in r.text


def test_recipient_city_and_cities(client):
    r = client.get("/api/cities")
    assert r.status_code == 200 and "Москва" in r.json()["cities"]
    body = {"giver_telegram_id": 111222333, "recipient_name": "Аня",
            "recipient_city": "Санкт-Петербург", "scene_ids": [12]}
    gid = client.post("/api/gifts", json=body).json()["gift_id"]
    g = client.get(f"/api/gifts/{gid}").json()["gift"]
    assert g["recipient_city"] == "Санкт-Петербург"


def test_shop_order_confirm_agency(client):
    h = {"X-Admin-Token": "test-admin"}
    shop = client.post("/api/shops", json={"name": "Агентская студия", "city": "Москва"}).json()["shop_id"]
    gid = _create_gift(client)
    bq = client.get("/api/bouquets", params={"city": "Москва"}).json()["bouquets"][0]
    o = client.post(f"/api/gifts/{gid}/order-bouquet",
                    json={"bouquet_id": bq["id"], "amount": 2000}).json()
    # студия подтверждает заказ по id Flowwow и ref
    r = client.post(f"/api/shops/{shop}/orders/confirm", headers=h, json={
        "flowwow_order_id": "FW-777", "ref_code": o["ref_code"], "amount": 2000})
    assert r.status_code == 200 and r.json()["commission_rub"] == 200.0
    orders = client.get(f"/api/shops/{shop}/orders?status=confirmed", headers=h).json()["orders"]
    assert any(x["flowwow_order_id"] == "FW-777" for x in orders)
    stats = client.get(f"/api/shops/{shop}/stats").json()
    assert stats["commission_rub"] >= 200.0


def test_verify_flow(client):
    # suggestion -> draft -> approve -> unverified -> verify
    sid = client.post("/api/scenes/suggest", json={
        "movie_title": "Фильм для сверки", "movie_year": 2010,
        "timecode_label": "0:10:00", "occasion_tags": ["просто так"],
        "description": "На проверку таймкода", "telegram_id": 111}).json()["scene_id"]
    h = {"X-Admin-Token": "test-admin"}
    client.post(f"/api/admin/scenes/{sid}/status", json={"status": "approved"}, headers=h)
    unv = client.get("/api/admin/scenes?status=approved&verified=0", headers=h).json()["scenes"]
    assert any(s["id"] == sid for s in unv)
    r = client.post(f"/api/admin/scenes/{sid}/verify", headers=h, json={
        "service": "kinopoisk", "timecode_label": "1:02:03", "timecode_seconds": 3723,
        "version_note": "лицензионное издание 2020", "watch_url": "https://www.kinopoisk.ru/film/123"})
    assert r.status_code == 200 and r.json()["verified"] is True
    s = client.get(f"/api/scenes/{sid}").json()
    assert s["timecode_label"] == "1:02:03" and s["timecode_seconds"] == 3723
    # ушла из unverified
    unv2 = client.get("/api/admin/scenes?status=approved&verified=0", headers=h).json()["scenes"]
    assert all(x["id"] != sid for x in unv2)


def test_film_export_premium(client):
    gid = _create_gift(client)
    r = client.get("/api/film/111222333/export")
    assert r.status_code == 402  # без премиума — 402
    # покупаем премиум
    p = client.post("/api/payments/create", json={"kind": "premium_film", "telegram_id": 111222333}).json()
    paid = client.post(f"/api/payments/{p['payment_id']}/confirm-dev").json()
    assert paid["status"] == "paid"
    r = client.get("/api/film/111222333/export")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Ваш фильм" in r.text and "подарков" in r.text


def test_import_scenes_script(client, tmp_path):
    import csv
    from scripts.import_scenes import import_scenes
    csv_path = tmp_path / "scenes.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["movie_title", "movie_year", "service", "timecode_label", "timecode_seconds",
                    "genre", "era", "age_rating", "mood_tags", "occasion_tags", "description",
                    "why_fits", "status"])
        w.writerow(["Импортированный фильм", 2015, "ivi", "0:33:00", 1980, "драма", "2010-е",
                    "12+", "драма; тепло", "поддержка", "Описание нашими словами", "Потому что", "draft"])
    n = import_scenes(str(csv_path))
    assert n == 1
    r = client.post("/api/scenes/search", json={"query": "импортированный"})
    # draft не показывается в публичном поиске
    assert all(s["movie_title"] != "Импортированный фильм" for s in r.json()["scenes"])


def test_catalog_seeded_big(client):
    r = client.post("/api/scenes/search", json={"limit": 50})
    assert r.json()["total"] >= 140  # большой каталог


def test_semantic_search(client):
    r = client.post("/api/scenes/semantic", json={"query": "сцена где извиняются перед любимым человеком", "limit": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["scenes"], "семантический поиск должен что-то найти"
    assert all(s["score"] > 0 for s in data["scenes"])
    # осмысленный запрос про 8 марта находит сцены с тегом 8 марта
    r = client.post("/api/scenes/semantic", json={"query": "что подарить маме на 8 марта тёплая сцена", "limit": 10})
    tags = [t for s in r.json()["scenes"] for t in s["occasion_tags"]]
    assert any("8 марта" in t for t in tags)


def test_lists(client):
    r = client.get("/api/lists")
    lists = r.json()["lists"]
    assert len(lists) >= 8
    l14 = next(l for l in lists if "14" in l["name"])
    assert l14["count"] > 0 and l14["scenes"]
    r = client.get(f"/api/lists/{l14['id']}")
    assert r.json()["scenes"]


def test_scene_of_the_day(client):
    r = client.get("/api/scenes/daily")
    assert r.status_code == 200 and r.json()["movie_title"]


def test_dates_ics(client):
    client.post("/api/dates", json={"name": "Мама", "date": "03-08", "remind_days": 7, "telegram_id": 111222333})
    r = client.get("/api/dates/111222333/ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in r.text and "RRULE:FREQ=YEARLY" in r.text and "Мама" in r.text


def test_card_preview_html(client):
    # выпускаем карточку через dev-эндпоинт магазина
    r = client.post("/api/shops/1/cards", json={"count": 10})
    assert r.status_code == 200
    code = r.json()["urls"][0].split("/")[-1]
    r = client.get(f"/api/cards/{code}/preview")
    assert r.status_code == 200 and "QR" in r.text or "svg" in r.text.lower()


def test_users_me_and_theme(client):
    r = client.get("/api/users/me?telegram_id=111222333")
    assert r.status_code == 200
    assert r.json()["premium"] in (True, False)
    gid = _create_gift(client, theme="neon")
    r = client.get(f"/api/gifts/{gid}")
    assert r.json()["gift"]["theme"] == "neon"


def test_gift_opened_notification_queued(client):
    from app.db import connect
    from app import config
    gid = _create_gift(client)
    r = client.get(f"/api/gifts/{gid}")
    assert r.json()["gift"]["status"] == "opened"
    conn = connect(config.DB_PATH)
    rows = conn.execute("SELECT * FROM notifications WHERE kind='gift_opened'").fetchall()
    conn.close()
    assert rows and rows[-1]["telegram_id"] == 111222333


def test_timecode_importer_and_review(client, tmp_path):
    """SRT -> кандидаты (без текста!) -> админ approve -> verified=1 у сцены."""
    import hashlib, json, sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import fetch_timecodes as ft

    srt = tmp_path / "demo.srt"
    srt.write_text(
        "1\n00:00:10,000 --> 00:00:12,000\nSome random intro line\n\n"
        "2\n01:23:45,000 --> 01:23:48,000\nYou cannot handle the truth moment here\n\n"
        "3\n01:30:00,000 --> 01:30:02,000\nAnother line about the truth and dreams\n\n",
        encoding="utf-8")
    blocks = ft.parse_srt(str(srt))
    assert len(blocks) == 3 and abs(blocks[1][1] - 5025) < 0.01
    picked = ft.keyword_moments(blocks, ["truth", "dreams"], top_n=2, min_gap=60)
    assert len(picked) == 2 and 5025.0 in [p["start"] for p in picked]
    assert ft.fmt_tc(5025) == "1:23:45"
    # семантический режим (RU): описание сцены находит по смыслу нужную строку
    ru_blocks = [(1, 100.0, 103.0, "Привет, как дела"), (2, 200.0, 204.0, "Я очень извиняюсь перед тобой за вчера"),
                 (3, 300.0, 302.0, "Погода сегодня отличная")]
    sem = ft.semantic_moments(ru_blocks, "сцена где герой извиняется за вчерашнее", lang="ru", top_n=1)
    assert sem and sem[0]["start"] == 200.0
    # консенсус: два файла, agree ±10 сек → consensus=2, confidence выше
    cc = ft.cluster_consensus([
        {"file": "a.srt", "source": "s1", "cand": {"score": 1.0, "line": 5, "start": 100.0, "text": "x"}},
        {"file": "b.srt", "source": "s2", "cand": {"score": 0.8, "line": 9, "start": 105.0, "text": "y"}},
        {"file": "c.srt", "source": "s3", "cand": {"score": 0.5, "line": 2, "start": 500.0, "text": "z"}},
    ])
    by_start = sorted(cc, key=lambda c: c["start"])
    assert by_start[0]["consensus"] == 2 and by_start[0]["confidence"] > by_start[0]["score"]
    assert by_start[1]["consensus"] == 1

    # manifest -> запись кандидатов в тестовую БД
    from app import config
    manifest = {"entries": [{
        "file": str(srt), "movie_title": "The Godfather", "movie_year": 1972,
        "service": "opensubtitles", "source": "https://example.com/demo.srt",
        "source_license": "test-license",
        "scenes": [{"match_title": "Крёстный отец", "keywords": ["truth", "dreams"],
                    "moment_label": "момент, где говорят правду"}],
    }]}
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    import subprocess
    r = subprocess.run([sys.executable, "scripts/fetch_timecodes.py", "--manifest", str(mpath),
                        "--db", config.DB_PATH], capture_output=True, text=True, cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert r.returncode == 0, r.stderr
    from app.db import connect
    conn = connect(config.DB_PATH)
    cands = conn.execute("SELECT * FROM timecode_candidates WHERE status='pending'").fetchall()
    conn.close()
    assert cands, "кандидаты должны появиться"
    assert all(len(c["evidence_sha1"]) == 40 for c in cands)  # sha1, а не текст
    assert all(c["moment_label"] == "момент, где говорят правду" for c in cands)
    # текст субтитров нигде не лежит в БД
    blob = "\n".join(str(v) for c in cands for v in dict(c).values())
    assert "cannot handle the truth" not in blob

    # админ: очередь + approve
    r = client.get("/api/admin/timecodes?status=pending", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200 and r.json()["candidates"]
    cid = r.json()["candidates"][0]["id"]
    r = client.post(f"/api/admin/timecodes/{cid}/review", headers={"X-Admin-Token": "test-admin"},
                    json={"action": "approve", "version_note": "сверено в плеере"})
    assert r.status_code == 200 and r.json()["ok"]
    r = client.post("/api/scenes/search", json={"query": "Крёстный отец", "limit": 5})
    sc = next(s for s in r.json()["scenes"] if s["movie_year"] == 1972)
    assert sc["verified"] == 1 and sc["timecode_label"]
    # без токена — 401/403
    r = client.get("/api/admin/timecodes")
    assert r.status_code in (401, 403)


def test_scenes_meta_categories(client):
    r = client.get("/api/scenes/meta")
    assert r.status_code == 200
    m = r.json()
    assert m["total"] >= 140
    assert m["occasions"] and m["moods"] and m["genres"] and m["eras"] and m["ages"]
    assert all("tag" in x and "count" in x for x in m["occasions"])
    assert any(o["tag"] == "8 марта" for o in m["occasions"])


def test_scenes_filters_and_sort(client):
    # фильтр по жанру
    r = client.post("/api/scenes/search", json={"genre": "мультфильм", "limit": 50})
    sc = r.json()["scenes"]
    assert sc and all(s["genre"] == "мультфильм" for s in sc)
    # фильтр по эпохе + сортировка по году
    r = client.post("/api/scenes/search", json={"era": "2000-е", "sort": "year_desc", "limit": 50})
    years = [s["movie_year"] for s in r.json()["scenes"]]
    assert years == sorted(years, reverse=True)
    # только с таймкодом / только проверенные
    r = client.post("/api/scenes/search", json={"has_timecode": True, "limit": 50})
    assert all(s["timecode_label"] for s in r.json()["scenes"])
    r = client.post("/api/scenes/search", json={"verified_only": True, "limit": 50})
    assert all(s["verified"] == 1 for s in r.json()["scenes"])
    # комбинация повод + настроение
    r = client.post("/api/scenes/search", json={"occasion": "8 марта", "mood": "романтика", "limit": 50})
    for s in r.json()["scenes"]:
        assert "8 марта" in [t.lower() for t in s["occasion_tags"]]
        assert "романтика" in [t.lower() for t in s["mood_tags"]]


def test_bulk_approve_consensus_and_moments(client):
    from app.db import connect
    from app import config
    conn = connect(config.DB_PATH)
    scene = conn.execute("SELECT id FROM scenes WHERE movie_title='Крёстный отец' AND movie_year=1972").fetchone()
    sid = scene["id"]
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    # два консенсусных кандидата (auto=1) + один слабый
    for tc, secs, conf, cons, auto in [("0:20:00", 1200, 1.2, 2, 1), ("1:00:00", 3600, 1.1, 2, 1), ("2:00:00", 7200, 0.3, 1, 0)]:
        conn.execute(
            "INSERT OR IGNORE INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
            " source, source_license, evidence_sha1, line_index, match_score, confidence, consensus, auto,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
            (sid, "opensubtitles", tc, secs, "test-src", "test-lic", "x" * 40, 1, conf, conf, cons, auto, now_iso))
    conn.commit()
    conn.close()
    r = client.post("/api/admin/timecodes/bulk-approve", headers={"X-Admin-Token": "test-admin"},
                    json={"min_confidence": 0.8, "min_consensus": 2, "only_auto": True})
    assert r.status_code == 200 and r.json()["approved"] == 2
    # моменты сцены видны публично
    r = client.get(f"/api/scenes/{sid}/moments")
    assert r.status_code == 200
    moments = r.json()["moments"]
    assert len(moments) >= 2 and all(m["timecode_label"] for m in moments)


def test_gift_timecode_snapshot_is_fixed(client):
    """Таймкод открытки фиксируется в момент создания: смена таймкода сцены в каталоге
    НЕ меняет уже созданный подарок."""
    from app.db import connect
    from app import config
    # Раньше в сидах лежала заглушка '0:00:00'; теперь реальный таймкод появляется
    # только после проверки. Ставим его сами, чтобы проверить снапшот открытки.
    conn = connect(config.DB_PATH)
    conn.execute("UPDATE scenes SET timecode_label='0:10:00', timecode_seconds=600, verified=1 WHERE id=12")
    conn.commit()
    conn.close()
    r = client.post("/api/scenes/search", json={"has_timecode": True, "limit": 5})
    sc = next(s for s in r.json()["scenes"] if s["id"] == 12)
    sid, tc = sc["id"], sc["timecode_label"]
    gid = _create_gift(client, scene_ids=[sid])
    r = client.get(f"/api/gifts/{gid}")
    assert r.json()["scenes"][0]["timecode_label"] == tc
    # меняем таймкод сцены в каталоге (как админ после пере-проверки)
    conn = connect(config.DB_PATH)
    conn.execute("UPDATE scenes SET timecode_label='9:59:59' WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    r = client.get(f"/api/gifts/{gid}")
    assert r.json()["scenes"][0]["timecode_label"] == tc, "снапшот должен остаться прежним"
    # выбор момента клиентом: moments={sid: другой одобренный таймкод}
    conn = connect(config.DB_PATH)
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
        " source, source_license, evidence_sha1, line_index, match_score, confidence, consensus, auto,"
        " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'approved',?)",
        (sid, "opensubtitles", "0:11:11", 671, "s", "l", "y" * 40, 1, 1.0, 1.0, 2, 1, now_iso))
    conn.commit()
    conn.close()
    gid2 = _create_gift(client, scene_ids=[sid], moments={str(sid): "0:11:11"})
    r = client.get(f"/api/gifts/{gid2}")
    assert r.json()["scenes"][0]["timecode_label"] == "0:11:11"


def test_timecode_segment_has_start_and_end(client):
    """Таймкод — это ОТРЕЗОК (начало+конец): кандидат из субтитров, auto-approve, момент, снапшот подарка."""
    from app.db import connect
    from app import config
    import datetime
    conn = connect(config.DB_PATH)
    sid = conn.execute("SELECT id FROM scenes WHERE movie_title='Начало' AND movie_year=2010").fetchone()["id"]
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO timecode_candidates (scene_id, service, timecode_label, timecode_seconds,"
        " timecode_end_label, timecode_end_seconds, source, source_license, evidence_sha1, line_index,"
        " match_score, confidence, consensus, auto, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
        (sid, "opensubtitles", "0:43:04", 2584, "0:43:31", 2611, "s", "l", "z" * 40, 1,
         1.0, 1.3, 2, 1, now_iso))
    conn.commit()
    conn.close()
    r = client.post("/api/admin/timecodes/bulk-approve", headers={"X-Admin-Token": "test-admin"},
                    json={"min_confidence": 0.8, "min_consensus": 2, "only_auto": True})
    assert r.json()["approved"] >= 1
    # сцена получила и начало, и конец
    r = client.post("/api/scenes/search", json={"query": "Начало", "limit": 10})
    sc = next(s for s in r.json()["scenes"] if s["movie_year"] == 2010)
    assert sc["timecode_label"] and sc.get("timecode_end_label"), "у сцены должен быть отрезок, не точка"
    # moments отдают отрезок
    r = client.get(f"/api/scenes/{sid}/moments")
    m = next(m for m in r.json()["moments"] if m["timecode_label"] == "0:43:04")
    assert m["timecode_end_label"] == "0:43:31"
    # подарок фиксирует отрезок снапшотом
    gid = _create_gift(client, scene_ids=[sid], moments={str(sid): "0:43:04"})
    r = client.get(f"/api/gifts/{gid}")
    s = r.json()["scenes"][0]
    assert s["timecode_label"] == "0:43:04" and s["timecode_end_label"] == "0:43:31"


def test_privacy_terms_pages(client):
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        assert r.status_code == 200 and "152-ФЗ" in r.text or "соглаш" in r.text.lower()


def test_gift_report_and_delete(client):
    gid = _create_gift(client)
    r = client.post(f"/api/gifts/{gid}/report", json={"reason": "спам", "telegram_id": 42})
    assert r.status_code == 200 and r.json()["ok"]
    r = client.get("/api/admin/reports", headers={"X-Admin-Token": "test-admin"})
    assert any(x["gift_id"] == gid for x in r.json()["reports"])
    # удаление дарителем (anon-режим: по telegram_id дарителя)
    r = client.post(f"/api/gifts/{gid}/delete?telegram_id=111222333")
    assert r.status_code == 200
    r = client.get(f"/api/gifts/{gid}")
    assert r.status_code == 404
    # чужой не удалит
    gid2 = _create_gift(client)
    r = client.post(f"/api/gifts/{gid2}/delete?telegram_id=999999")
    assert r.status_code == 403


def test_payment_webhook_auth_prod_mode(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "YUKASSA_SHOP_ID", "12345")
    monkeypatch.setattr(config, "YUKASSA_SECRET_KEY", "secret")
    r = client.post("/api/payments/webhook", json={"event": "payment.succeeded"})
    assert r.status_code == 403
    import base64
    tok = base64.b64encode(b"12345:secret").decode()
    r = client.post("/api/payments/webhook", json={"event": "ping"},
                    headers={"Authorization": f"Basic {tok}"})
    assert r.status_code == 200
    monkeypatch.setattr(config, "YUKASSA_SHOP_ID", "")
    monkeypatch.setattr(config, "YUKASSA_SECRET_KEY", "")


def test_upload_limit(monkeypatch, client, tmp_path):
    from app import config
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)  # любой файл больше 0 МБ
    monkeypatch.setattr(config, "MEDIA_DIR", str(tmp_path))
    r = client.post("/api/upload", files={"file": ("a.png", b"x" * 1024, "image/png")})
    assert r.status_code == 413


def test_analytics_events_and_export(client):
    gid = _create_gift(client)
    client.get(f"/api/gifts/{gid}")  # opened
    client.post(f"/api/gifts/{gid}/reply", json={"type": "reaction", "reaction": "❤️"})
    r = client.get("/api/admin/analytics", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
    f = r.json()["funnel"]
    assert f["gift_created"] >= 1 and f["gift_opened"] >= 1 and f["gift_replied"] >= 1
    # экспорт данных пользователя (152-ФЗ)
    r = client.get("/api/users/111222333/export")
    assert r.status_code == 200
    d = r.json()
    assert d["user"]["telegram_id"] == 111222333
    assert any(g["id"] == gid for g in d["gifts"])
    assert d["consents"], "согласия должны быть в экспорте"


def test_scheduler_gift_soon_and_media_cleanup(tmp_path):
    import datetime
    from app import config
    from app.db import connect
    from app import scheduler
    conn = connect(config.DB_PATH)
    soon = (scheduler.now() + datetime.timedelta(hours=20)).isoformat()
    conn.execute(
        "INSERT INTO gifts (id, recipient_name, title, occasion, tone, status, created_at, open_at, soon_notified)"
        " VALUES ('soon1','Аня',NULL,'просто так','тёплый','created',?, ?,0)",
        (scheduler.now().isoformat(), soon))
    # старый удалённый подарок с медиа-файлом
    import os
    os.makedirs(config.MEDIA_DIR, exist_ok=True)
    media_file = os.path.join(config.MEDIA_DIR, "oldvoice.webm")
    open(media_file, "wb").write(b"x")
    old = (scheduler.now() - datetime.timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO gifts (id, recipient_name, status, created_at, message_voice_file_id)"
        " VALUES ('old1','Боб','deleted',?, '/media/oldvoice.webm')", (old,))
    conn.commit()
    scheduler._gift_soon(conn)
    scheduler._cleanup_media(conn)
    conn.commit()
    assert conn.execute("SELECT soon_notified FROM gifts WHERE id='soon1'").fetchone()[0] == 1
    assert not os.path.exists(media_file), "медиа удалённого подарка должно очиститься"
    assert conn.execute("SELECT message_voice_file_id FROM gifts WHERE id='old1'").fetchone()[0] is None
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE kind='gift_soon'").fetchone()[0]
    conn.close()
