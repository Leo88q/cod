/* «Сцена» — Mini App: роутер и экраны (создание, открытка, таймлайн, каталог, даты, фильм, букеты, кабинет студии). */
(function () {
  const TG = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (TG) { TG.ready(); TG.expand(); }
  const tgUser = TG && TG.initDataUnsafe ? TG.initDataUnsafe.user : null;
  const TG_ID = tgUser ? tgUser.id : null;

  const app = document.getElementById("app");
  const toastEl = document.getElementById("toast");
  let toastTimer = null;
  let stopRecordingOnRoute = null;
  const toast = (msg) => {
    toastEl.textContent = msg; toastEl.classList.remove("hidden");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 2600);
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, { method = "GET", body, headers: extra } = {}) {
    const headers = { ...(extra || {}) };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (TG && TG.initData) headers["X-Telegram-Init-Data"] = TG.initData;
    const res = await fetch(path, {
      method, headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const messages = {402: "Нужна оплата. Откройте раздел оплаты и проверьте её статус.",
        403: "Нет доступа. Откройте приложение заново из Telegram.",
        404: "Не найдено. Возможно, объект удалён.", 410: "Срок действия истёк. Создайте новый подарок.",
        429: "Слишком много запросов. Повторите через " + (res.headers.get("Retry-After") || "60") + " сек."};
      const detail = typeof data.detail === "string" ? data.detail : "Проверьте заполнение полей.";
      const error = new Error(messages[res.status] || (res.status >= 500 ? "Ошибка сервера. Попробуйте позже." : detail));
      error.status = res.status;
      error.requestId = res.headers.get("X-Request-ID");
      throw error;
    }
    return res.json();
  }

  const openLink = (url) => {
    const parsed = new URL(url, location.href);
    if (!["http:", "https:"].includes(parsed.protocol)) return;
    if (TG && parsed.hostname === "t.me" && TG.openTelegramLink) TG.openTelegramLink(parsed.href);
    else if (TG && TG.openLink) TG.openLink(parsed.href);
    else window.open(parsed.href, "_blank", "noopener,noreferrer");
  };

  // Поиск фильма в легальном плеере для ручной сверки таймкода.
  const PLAYER_SEARCH = {
    kinopoisk: q => `https://www.kinopoisk.ru/index.php?kp_query=${encodeURIComponent(q)}`,
    ivi: q => `https://www.ivi.ru/search/?q=${encodeURIComponent(q)}`,
    okko: q => `https://okko.tv/search?query=${encodeURIComponent(q)}`,
    youtube: q => `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`,
  };

  // ---------- состояние ----------
  const state = { selected: new Set(), bookmarks: new Set(), gift: null, replyScene: null, replyMode: "message", moments: {} };

  const OCCASIONS = ["просто так", "день рождения", "годовщина", "признание", "извинение",
    "поддержка", "благодарность", "день святого валентина", "8 марта", "23 февраля",
    "новый год", "свадьба", "рождение ребёнка", "для старших", "для детей"];
  const MOODS = ["романтика", "юмор", "ностальгия", "драма", "тепло", "вдохновение", "грусть", "мощь"];
  const TONES = ["тёплый", "романтичный", "игривый", "лаконичный"];
  const CITIES = ["Москва", "Санкт-Петербург"];

  // ---------- роутер ----------
  async function route() {
    if (stopRecordingOnRoute) { stopRecordingOnRoute(); stopRecordingOnRoute = null; }
    clearInterval(countdownTimer);
    app.setAttribute("aria-busy", "true");
    app.innerHTML = '<section class="loading" role="status">Загружаем…</section>';
    try {
      await renderRoute();
    } catch (e) {
      app.innerHTML = `<section class="view panel" role="alert"><h2>Не удалось загрузить</h2>
        <p>${esc(e.message || "Проверьте соединение с интернетом.")}</p>
        ${e.requestId ? `<p class="muted">Код для поддержки: ${esc(e.requestId)}</p>` : ""}
        <button class="btn primary" id="route-retry">Повторить</button>
        <button class="btn ghost" id="route-home">На главную</button></section>`;
      document.getElementById("route-retry").onclick = route;
      document.getElementById("route-home").onclick = () => { location.hash = "#/create"; };
    } finally {
      app.setAttribute("aria-busy", "false");
    }
  }
  window.addEventListener("unhandledrejection", event => {
    event.preventDefault();
    toast(event.reason && event.reason.message ? event.reason.message : "Не удалось выполнить действие. Повторите позже.");
  });
  function renderRoute() {
    const h = location.hash.replace(/^#\/?/, "");
    const [path, queryStr] = h.split("?");
    const q = new URLSearchParams(queryStr || "");
    document.querySelectorAll(".nav-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.nav === "/" + path.split("/")[0]));
    document.getElementById("topnav").classList.remove("hidden");
    const parts = path.split("/").filter(Boolean);
    if (parts.length === 0 || parts[0] === "create") return renderCreate(q);
    if (parts[0] === "g") return renderGift(parts[1]);
    if (parts[0] === "card") return renderCard(parts[1]);
    if (parts[0] === "timeline") return renderTimeline();
    if (parts[0] === "catalog") return renderCatalog(q);
    if (parts[0] === "dates") return renderDates();
    if (parts[0] === "film") return renderFilm(q);
    if (parts[0] === "bouquets") return renderBouquets(q);
    if (parts[0] === "shop") return renderShop(q);
    if (parts[0] === "admin") return renderAdmin();
    if (parts[0] === "pay") return renderPay(parts[1]);
    renderCreate(q);
  }
  window.addEventListener("hashchange", route);
  document.querySelectorAll(".nav-btn").forEach((b) =>
    (b.onclick = () => { location.hash = "#" + b.dataset.nav; }));

  // ---------- общие блоки ----------
  function hasRealTimecode(s) {
    // Раньше в сидах лежала строка-заглушка '0:00:00' без секунд — это НЕ таймкод.
    if (!s || !s.timecode_label) return false;
    if (s.timecode_label === "0:00:00" && s.timecode_seconds == null && !s.verified) return false;
    return true;
  }
  function tcRange(s) {
    if (!hasRealTimecode(s)) return "—";
    return s.timecode_end_label ? `${s.timecode_label}–${s.timecode_end_label}` : s.timecode_label;
  }

  function sceneCard(s, { selectable } = {}) {
    const sel = state.selected.has(s.id);
    return `<div class="scene-item ${sel ? "selected" : ""}" data-scene="${s.id}">
      <button class="star" data-star="${s.id}" title="В закладки">${state.bookmarks.has(s.id) ? "⭐" : "☆"}</button>
      <div class="t">${esc(s.movie_title)} (${esc(s.movie_year)}) · <span class="tc">${esc(tcRange(s))}${s.verified ? ' <span class="vbadge" title="Таймкод проверен в легальном плеере">✓</span>' : ""}</span></div>
      <div class="d">${esc(s.description)}</div>
      ${s.why_fits ? `<div class="d" style="font-style:italic">${esc(s.why_fits)}</div>` : ""}
      <div class="tags">${(s.occasion_tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    </div>`;
  }

  async function searchScenes(params) {
    const data = await api("/api/scenes/search", { method: "POST", body: params });
    return data.scenes;
  }

  async function loadBookmarks() {
    if (!TG_ID) return;
    try {
      const d = await api(`/api/bookmarks?telegram_id=${TG_ID}`);
      state.bookmarks = new Set(d.scenes.map((s) => s.id));
    } catch (e) { /* anon режим */ }
  }

  function attachSceneListClicks(container, onPick) {
    container.querySelectorAll(".scene-item").forEach((el) => {
      el.addEventListener("click", (ev) => {
        if (ev.target.dataset.star) return;
        const id = Number(el.dataset.scene);
        if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
        el.classList.toggle("selected");
        if (onPick) onPick(id);
      });
    });
    container.querySelectorAll(".star").forEach((btn) => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const id = Number(btn.dataset.star);
        try {
          if (state.bookmarks.has(id)) {
            await api(`/api/bookmarks/${id}?telegram_id=${TG_ID}`, { method: "DELETE" });
            state.bookmarks.delete(id); btn.textContent = "☆";
          } else {
            await api("/api/bookmarks", { method: "POST", body: { scene_id: id, telegram_id: TG_ID } });
            state.bookmarks.add(id); btn.textContent = "⭐";
          }
        } catch (e) { toast("Войдите через Telegram, чтобы сохранять закладки"); }
      });
    });
  }

  function chips(list, active, onClick) {
    return `<div class="chips">${list.map((c) =>
      `<button class="chip ${c === active ? "active" : ""}" data-chip="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
  }

  // ---------- экран создания ----------
  let createOccasion = "просто так";
  let createMood = "";

  async function renderCreate(q) {
    const card = q.get("card");
    app.innerHTML = `
      <section class="view">
        <h1 class="logo">🎬 Сцена</h1>
        <p class="subtitle">${card ? "Создай подарок по карточке из букета 🌷" : "Подари момент из фильма"}</p>
        <label class="field"><span>Заголовок открытки</span>
          <input id="f-title" placeholder="Например: С 8 марта, любимая 🌷" maxlength="80"></label>
        <label class="field"><span>Кому?</span>
          <input id="f-recipient" placeholder="Например: Аня" maxlength="60"></label>
        <label class="field"><span>Город получателя (для букета)</span>
          <select id="f-city"><option value="">— выбрать позже —</option></select></label>
        <label class="field"><span>Повод</span><select id="f-occasion">
          ${OCCASIONS.map((o) => `<option ${o === createOccasion ? "selected" : ""}>${o}</option>`).join("")}
        </select></label>
        <label class="field"><span>Тон</span><select id="f-tone">
          ${TONES.map((t) => `<option>${t}</option>`).join("")}
        </select></label>
        <label class="field"><span>Формат</span>
          <select id="f-format">
            <option value="single">Одна сцена</option>
            <option value="series">Серия на несколько дней</option>
          </select></label>
        <label class="field hidden" id="f-days-wrap"><span>Дней в серии</span>
          <input id="f-days" type="number" min="2" max="14" value="3"></label>
        <label class="field"><span>Открыть не раньше (необязательно)</span>
          <input id="f-openat" type="datetime-local"></label>
        <div class="field"><span>Подобрать сцены под повод</span>
          <div class="row">
            <button id="btn-pick" class="btn ghost">🔍 Подобрать</button>
            <input id="f-q" class="search-input" style="max-width:180px" placeholder="поиск: извиниться…">
          </div>
          <div id="f-moods">${chips(MOODS, createMood, () => {})}</div>
          <div class="muted">Выбрано сцен: <b id="sel-count">0</b></div>
          <div id="scene-list" class="scene-list"></div>
          <div id="moment-picker"></div>
        </div>
        <label class="field"><span>Ваше сообщение</span>
          <textarea id="f-message" rows="3" maxlength="500" placeholder="Пара тёплых слов…"></textarea></label>
        <div class="field"><span>Или запиши голосовое 🎙️</span>
          <div class="row">
            <button id="btn-rec" class="btn ghost small">● Записать</button>
            <span id="rec-status" class="muted"></span>
            <audio id="rec-preview" controls class="hidden" style="height:32px"></audio>
          </div></div>
        <label class="field"><span>Фото к открытке (необязательно)</span>
          <input id="f-photo" type="file" accept="image/*"></label>
        <label class="field" style="display:flex;gap:8px;align-items:flex-start">
          <input id="f-consent" type="checkbox" style="margin-top:4px">
          <span>Соглашаюсь с <a href="/privacy" target="_blank">политикой обработки данных</a> и
          <a href="/terms" target="_blank">условиями сервиса</a> (152-ФЗ)</span></label>
        <div class="field"><span>Тема открытки</span>
          <div class="row" id="theme-row">
            <button class="chip active" data-theme="cinema">🎬 Кино</button>
            <button class="chip" data-theme="retro">📼 Ретро</button>
            <button class="chip" data-theme="neon">🌃 Неон</button>
            <button class="chip" data-theme="paper">📜 Бумага ⭐</button>
          </div>
          <span class="muted" id="theme-hint">Тема «Бумага» — в премиуме (499 ₽/год)</span></div>
        <button id="btn-create" class="btn primary">Создать подарок ✨</button>
        <div id="create-result" class="result hidden"></div>
      </section>`;
    document.getElementById("f-occasion").onchange = (e) => (createOccasion = e.target.value);
    api("/api/cities").then((d) => {
      const sel = document.getElementById("f-city");
      (d.cities || []).forEach((c) => {
        const o = document.createElement("option"); o.value = c; o.textContent = c; sel.appendChild(o);
      });
    }).catch(() => {});
    document.getElementById("f-format").onchange = (e) =>
      document.getElementById("f-days-wrap").classList.toggle("hidden", e.target.value !== "series");
    document.getElementById("f-moods").addEventListener("click", (e) => {
      if (!e.target.dataset.chip) return;
      createMood = createMood === e.target.dataset.chip ? "" : e.target.dataset.chip;
      document.querySelectorAll("#f-moods .chip").forEach((c) =>
        c.classList.toggle("active", c.dataset.chip === createMood));
    });
    document.getElementById("btn-pick").onclick = async () => {
      const scenes = await searchScenes({ occasion: createOccasion, mood: createMood || null, query: document.getElementById("f-q").value || null, limit: 12 });
      const list = document.getElementById("scene-list");
      list.innerHTML = scenes.length ? scenes.map((s) => sceneCard(s)).join("")
        : '<div class="muted">Ничего не нашлось — попробуй другой повод или поиск</div>';
      attachSceneListClicks(list, () => {
        document.getElementById("sel-count").textContent = state.selected.size;
        renderMomentPicker();
      });
    };
    // голосовое сообщение через MediaRecorder
    let recorder = null, recChunks = [], recording = false, voicePath = null;
    let recordingTimer = null, previewURL = null, discarded = false;
    stopRecordingOnRoute = () => {
      discarded = true;
      clearTimeout(recordingTimer);
      if (recorder) {
        if (recorder.state !== 'inactive') recorder.stop();
        recorder.stream.getTracks().forEach(track => track.stop());
      }
      if (previewURL) URL.revokeObjectURL(previewURL);
    };
    const recBtn = document.getElementById("btn-rec");
    const recStatus = document.getElementById("rec-status");
    const recPrev = document.getElementById("rec-preview");
    recBtn.onclick = async () => {
      if (!recording) {
        try { recChunks = []; recorder = new MediaRecorder(await navigator.mediaDevices.getUserMedia({ audio: true })); }
        catch (e) { recStatus.textContent = "Нет доступа к микрофону"; return; }
        if (discarded) { recorder.stream.getTracks().forEach(track => track.stop()); return; }
        voicePath = null;
        document.getElementById('btn-create').disabled = true;
        recorder.ondataavailable = (e) => recChunks.push(e.data);
        recorder.onstop = async () => {
          clearTimeout(recordingTimer);
          recording = false;
          recorder.stream.getTracks().forEach(track => track.stop());
          recBtn.textContent = '● Записать';
          if (discarded) return;
          const mime = recorder.mimeType || 'audio/webm';
          const blob = new Blob(recChunks, { type: mime });
          if (previewURL) URL.revokeObjectURL(previewURL);
          previewURL = URL.createObjectURL(blob);
          recPrev.src = previewURL;
          recPrev.classList.remove("hidden");
          const fd = new FormData();
          const extension = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
          fd.append("file", blob, "voice." + extension);
          recStatus.textContent = 'Обрабатываем голосовое…';
          recBtn.disabled = true;
          try {
            const up = await fetch("/api/upload", { method: "POST", body: fd });
            if (!up.ok) throw new Error('Не удалось загрузить голосовое');
            voicePath = (await up.json()).path;
            recStatus.textContent = "Голосовое загружено ✔";
          } catch (e) {
            voicePath = null;
            recStatus.textContent = 'Не удалось загрузить голосовое. Повторите запись.';
          } finally {
            recBtn.disabled = false;
            if (!discarded && document.getElementById('btn-create')) document.getElementById('btn-create').disabled = false;
          }
        };
        recorder.start();
        recordingTimer = setTimeout(() => { if (recorder.state !== 'inactive') recorder.stop(); }, 300000);
        recording = true;
        recBtn.textContent = "■ Стоп";
        recStatus.textContent = "Идёт запись…";
      } else {
        recorder.stop();
        recording = false;
        recBtn.textContent = "● Записать";
      }
    };

    // тема открытки (премиум-тема — с подпиской)
    let theme = "cinema";
    let premium = false;
    try { premium = (await api("/api/users/me?telegram_id=" + TG_ID)).premium; } catch (e) {}
    const themeHint = document.getElementById("theme-hint");
    if (premium) themeHint.textContent = "У вас премиум — все темы доступны ✨";
    document.getElementById("theme-row").addEventListener("click", (e) => {
      const t = e.target.dataset && e.target.dataset.theme;
      if (!t) return;
      if (t === "paper" && !premium) {
        themeHint.textContent = "Тема «Бумага» — с премиумом (499 ₽/год). Сейчас будет обычная тема";
        return;
      }
      theme = t;
      document.querySelectorAll("#theme-row .chip").forEach((c) => c.classList.toggle("active", c.dataset.theme === t));
    });

    document.getElementById("btn-create").onclick = async () => {
      if (!state.selected.size) return toast("Выбери хотя бы одну сцену 👆");
      let photo = null;
      const file = document.getElementById("f-photo").files[0];
      if (file) {
        const fd = new FormData(); fd.append("file", file);
        const up = await fetch("/api/upload", { method: "POST", body: fd });
        if (!up.ok) { toast("Не удалось загрузить фото. Повторите попытку."); return; }
        photo = (await up.json()).path;
      }
      const openAt = document.getElementById("f-openat").value;
      const body = {
        giver_telegram_id: TG_ID,
        recipient_name: document.getElementById("f-recipient").value || "Дорогой человек",
        recipient_city: document.getElementById("f-city").value || null,
        title: document.getElementById("f-title").value || null,
        occasion: createOccasion, tone: document.getElementById("f-tone").value,
        scene_ids: [...state.selected], moments: state.moments,
        message_text: document.getElementById("f-message").value || null,
        message_photo: photo,
        format: document.getElementById("f-format").value,
        series_days: Number(document.getElementById("f-days").value) || 1,
        open_at: openAt ? new Date(openAt).toISOString() : null,
        card_code: card || null,
        theme, message_voice: voicePath,
        consent: document.getElementById("f-consent").checked,
      };
      try {
        const data = await api("/api/gifts", { method: "POST", body });
        const res = document.getElementById("create-result");
        res.classList.remove("hidden");
        res.innerHTML = `<p><b>Готово! 🎁</b> Отправь ссылку получателю:</p>
          <a class="gift-link" href="${esc(data.open_url)}">${esc(data.open_url)}</a>
          <div class="qr-box"><img alt="QR" src="${esc(data.qr)}"></div>
          <div class="row">
            <button id="btn-copy" class="btn ghost small">📋 Копировать</button>
            <button id="btn-share" class="btn ghost small">📤 Поделиться</button>
            <button id="btn-open" class="btn primary small">Открыть</button>
          </div>`;
        document.getElementById("btn-copy").onclick = async () => {
          try { await navigator.clipboard.writeText(data.open_url); toast("Скопировано 📋"); }
          catch (e) { toast(data.open_url); }
        };
        document.getElementById("btn-share").onclick = () =>
          openLink("https://t.me/share/url?url=" + encodeURIComponent(data.open_url) +
            "&text=" + encodeURIComponent("Тебе подарили сцену из фильма 🎬"));
        document.getElementById("btn-open").onclick = () => { location.hash = "#/g/" + data.gift_id; };
        if (TG && TG.sendData) TG.sendData(JSON.stringify({ gift_id: data.gift_id }));
        toast("Подарок создан 🎬");
      } catch (e) { toast("Ошибка: " + e.message); }
    };
    if (card) {
      try {
        const c = await api("/api/cards/" + card);
        if (c.status === "used") toast("Эта карточка уже использована");
        else toast(`Карточка студии «${c.shop_name}» (${c.shop_city || "РФ"}) 🌷`);
      } catch (e) { toast("Карточка не найдена"); }
    }
    loadBookmarks();
  }

  // ---------- экран открытки ----------
  let countdownTimer = null;

  async function renderGift(code) {
    let data;
    try { data = await api("/api/gifts/" + code); }
    catch (e) {
      app.innerHTML = `<section class="view"><div class="panel lock-card">
        <div class="lock-emoji">🎁</div><h2 class="h2">Подарок не найден</h2>
        <p class="muted">${esc(e.message)}</p></div></section>`;
      return;
    }
    if (data.locked) {
      app.innerHTML = `<section class="view"><div class="panel lock-card">
        <div class="lock-emoji">🔒</div><h2 class="h2">Откроется позже</h2>
        <p class="muted">Для ${esc(data.gift.recipient_name)} · повод: ${esc(data.gift.occasion || "просто так")}</p>
        <div class="countdown" id="cd"></div></section></section>`;
      const target = new Date(data.open_at);
      const tick = () => {
        const ms = target - new Date();
        if (ms <= 0) { clearInterval(countdownTimer); renderGift(code); return; }
        const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, m = Math.floor(ms / 6e4) % 60;
        document.getElementById("cd").textContent = `${d} дн ${h} ч ${m} мин`;
      };
      tick(); countdownTimer = setInterval(tick, 30000);
      return;
    }
    clearInterval(countdownTimer);
    const g = data.gift;
    if (g.view_count === 1) confetti();  // 🎉 первый просмотр — празднуем
    state.gift = code;
    const scenesHtml = data.scenes.map((s, i) => `
      <div class="gift-card">
        ${data.scenes.length > 1 ? `<div class="film-badge">🎞️ escena ${i + 1} из ${data.scenes.length}</div>` : '<div class="film-badge">🎞️ из фильма</div>'}
        <h1 class="movie-title">${esc(s.movie_title)} (${esc(s.movie_year)})</h1>
        <div class="timecode">⏱ ${esc(tcRange(s))}</div>
        <p class="scene-desc">${esc(s.description)}</p>
        ${s.why_fits ? `<p class="scene-why">${esc(s.why_fits)}</p>` : ""}
        <div class="row" style="margin-top:12px">
          ${(s.watch_links || []).slice(0, 4).map((w, wi) =>
            `<button class="btn ${wi === 0 ? "primary" : "ghost"} small watch-btn" data-url="${esc(w.url)}">▶ ${esc(w.service)}</button>`).join("")}
          <button class="btn ghost small copy-tc" data-tc="${esc(tcRange(s))}">⏱ скопировать</button>
        </div>
        ${hasRealTimecode(s)
          ? `<p class="muted" style="margin-top:8px">Если таймкод не совпал — перемотай на ${esc(tcRange(s))} вручную.</p>`
          : `<p class="muted" style="margin-top:8px">Таймкод ещё не проверен модератором — момент уточняется.</p>`}
      </div>`).join("");
    app.innerHTML = `
      <section class="view theme-${esc(g.theme || "cinema")}">
        <h1 class="logo" style="font-size:26px">${esc(g.title || "Тебе — сцена из фильма 🎬")}</h1>
        ${scenesHtml}
        ${g.message_text ? `<div class="message-box">💌 ${esc(g.message_text)}</div>` : ""}
        ${g.message_photo_url ? `<img class="gift-photo" src="${esc(g.message_photo_url)}">` : ""}
        ${g.message_voice_url ? `<div class="voice-note">🎙️ <audio controls src="${esc(g.message_voice_url)}"></audio></div>` : ""}
        <div class="gift-meta">для <b>${esc(g.recipient_name)}</b> · ${esc(g.occasion || "просто так")} · тон: ${esc(g.tone || "тёплый")} · просмотров: ${g.view_count}</div>
        ${data.locked_count ? `<p class="muted">🔒 Ещё сцен в серии: ${data.locked_count} — откроются завтра</p>` : ""}
        <div class="panel">
          <h3>Ответить дарителю</h3>
          <div class="reply-block">
            <span>Реакция:</span>
            <div class="reactions">${["❤️", "😂", "😮", "😢", "👏"].map((r) =>
              `<button class="reaction-btn" data-r="${r}">${r}</button>`).join("")}</div>
          </div>
          <div class="reply-block" style="margin-top:8px">
            <button class="btn ghost small" id="btn-r-scene">🎬 Своей сценой</button>
            <button class="btn ghost small" id="btn-r-text">✉️ Сообщением</button>
          </div>
          <div id="reply-scene-list" class="scene-list hidden" style="margin-top:8px"></div>
          <div id="reply-box" class="hidden" style="margin-top:8px">
            <textarea id="reply-text" rows="2" maxlength="500" placeholder="Твой ответ…"></textarea>
            <button id="btn-send-reply" class="btn primary small">Отправить 💫</button>
          </div>
        </div>
        <div class="panel"><h3>💞 Ваш таймлайн</h3>
          <div id="replies">${data.replies.map((r) => `
            <div class="reply-item"><span class="who">${esc(r.respondent || "Получатель")} · ${r.type === "scene" ? "🎬 сцена" : r.type === "reaction" ? "реакция" : "✉️"}</span><br>
            ${r.type === "scene" ? esc(r.movie_title || "") + " ⏱ " + esc(r.timecode_label || "") : r.type === "reaction" ? esc(r.reaction || "") : esc(r.message_text || "")}
            </div>`).join("") || '<span class="muted">Пока пусто — будь первым 💫</span>'}</div>
          <div class="row" style="margin-top:8px">
            <input id="note-text" class="search-input" placeholder="Заметка к моменту…">
            <button id="btn-note" class="btn ghost small">＋</button>
          </div>
          <div id="notes">${data.notes.map((n) => `<div class="reply-item">📝 ${esc(n.text || "")}</div>`).join("")}</div>
        </div>
        <button id="btn-bouquet" class="btn primary">🌷 Добавить букет к подарку</button>
        <button id="btn-gift-back" class="btn ghost">🎁 Ответить своим подарком (сцена + букет)</button>
      </section>`;
    const actions = document.createElement("div");
    actions.className = "panel row";
    actions.innerHTML = `<button class="btn ghost" id="gift-report">Пожаловаться</button>
      ${TG_ID ? '<button class="btn ghost" id="gift-delete">Удалить мой подарок</button>' : ''}
      <button class="btn ghost" id="media-refresh">Обновить медиа</button>`;
    app.appendChild(actions);
    document.getElementById("media-refresh").onclick = () => renderGift(code);
    document.getElementById("gift-report").onclick = async () => {
      const reason = window.prompt("Что не так с подарком? Не указывайте лишние персональные данные.");
      if (!reason || !reason.trim()) return;
      try {
        await api(`/api/gifts/${code}/report`, {method: "POST", body: {reason: reason.trim(), telegram_id: TG_ID}});
        toast("Жалоба отправлена на рассмотрение");
      } catch (e) { toast(e.message); }
    };
    const deleteButton = document.getElementById("gift-delete");
    if (deleteButton) deleteButton.onclick = async () => {
      if (!window.confirm("Удалить подарок? Его ссылка перестанет работать. Удалить может только даритель.")) return;
      try {
        await api(`/api/gifts/${code}/delete?telegram_id=${TG_ID}`, {method: "POST"});
        location.hash = "#/timeline";
      } catch (e) { toast(e.message); }
    };
    app.querySelectorAll(".watch-btn").forEach((b) =>
      (b.onclick = () => openLink(b.dataset.url)));
    app.querySelectorAll(".copy-tc").forEach((b) =>
      (b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.tc || b.textContent.trim()); toast("Таймкод скопирован ⏱"); } catch (e) {} }));
    app.querySelectorAll(".reaction-btn").forEach((b) =>
      (b.onclick = () => sendReply("reaction", null, b.dataset.r, null)));
    document.getElementById("btn-r-text").onclick = () => {
      state.replyMode = "message";
      document.getElementById("reply-box").classList.remove("hidden");
    };
    document.getElementById("btn-r-scene").onclick = async () => {
      state.replyMode = "scene";
      document.getElementById("reply-box").classList.remove("hidden");
      const list = document.getElementById("reply-scene-list");
      list.classList.remove("hidden");
      const scenes = await searchScenes({ query: document.getElementById("f-q") ? "" : "", limit: 8 });
      list.innerHTML = scenes.map((s) => sceneCard(s)).join("");
      attachSceneListClicks(list, (id) => { state.replyScene = id; });
    };
    document.getElementById("btn-send-reply").onclick = async () => {
      const text = document.getElementById("reply-text").value || null;
      await sendReply(state.replyMode, state.replyScene, null, text);
    };
    document.getElementById("btn-note").onclick = async () => {
      const text = document.getElementById("note-text").value;
      if (!text) return;
      await api(`/api/gifts/${code}/notes`, { method: "POST", body: { author_telegram_id: TG_ID, text } });
      toast("Заметка добавлена 📝"); renderGift(code);
    };
    document.getElementById("btn-bouquet").onclick = () => { location.hash = "#/bouquets?g=" + code; };
    const back = document.getElementById("btn-gift-back");
    if (back) back.onclick = () => { location.hash = "#/create"; };
  }

  async function sendReply(type, sceneId, reaction, text) {
    try {
      await api(`/api/gifts/${state.gift}/reply`, {
        method: "POST",
        body: { respondent: tgUser ? tgUser.first_name : "Получатель", respondent_telegram_id: TG_ID, type, scene_id: sceneId, reaction, message_text: text },
      });
      toast("Ответ отправлен 💫"); renderGift(state.gift);
    } catch (e) { toast("Не удалось отправить: " + e.message); }
  }

  // ---------- карточка студии ----------
  async function renderCard(code) {
    try {
      const c = await api("/api/cards/" + code);
      app.innerHTML = `<section class="view">
        <h1 class="logo">🎬 Сцена</h1>
        <div class="panel" style="text-align:center">
          <div style="font-size:44px">🌷</div>
          <h2 class="h2">Карточка из букета</h2>
          <p class="muted">Студия «${esc(c.shop_name)}» · ${esc(c.shop_city || "Россия")}</p>
          ${c.status === "used"
            ? `<p class="muted" style="margin-top:8px">Эта сцена уже открыта 🎬</p>
               <button class="btn primary" onclick="location.hash='#/g/${esc(c.gift_code)}'">Открыть подарок</button>`
            : `<div class="qr-box"><img src="/api/cards/${esc(code)}/qr.svg" alt="QR"></div>
               <button class="btn primary" onclick="location.hash='#/create?card=${esc(code)}'">✨ Создать подарок для получателя</button>`}
        </div></section>`;
    } catch (e) {
      app.innerHTML = `<section class="view"><div class="panel lock-card"><div class="lock-emoji">🃏</div>
        <h2 class="h2">Карточка не найдена</h2><p class="muted">${esc(e.message)}</p></div></section>`;
    }
  }

  // ---------- таймлайн ----------
  async function renderTimeline() {
    if (!TG_ID) {
      app.innerHTML = `<section class="view"><div class="panel">Открой «Сцену» из Telegram, чтобы видеть свой таймлайн 💞</div></section>`;
      return;
    }
    try {
      const d = await api(`/api/timeline/${TG_ID}`);
      app.innerHTML = `<section class="view">
        <h2 class="h2">💞 Мой таймлайн</h2>
        <p class="muted">Подарков создано: ${d.total} ${d.peers.length ? "· с тобой в паре: " + d.peers.map((p) => esc(p.first_name || p.telegram_id)).join(", ") : ""}</p>
        <div class="panel"><h3>Связать таймлайн с близким</h3>
          <div class="row"><input id="peer" class="search-input" placeholder="его Telegram ID">
          <button id="btn-peer" class="btn ghost small">＋</button></div></div>
        <div class="row"><button class="btn ghost small" onclick="location.hash='#/film'">🎞️ Ваш фильм года</button></div>
        ${d.items.map((it) => `<div class="tl-item">
          <div class="when">${esc(it.gift.created_at.slice(0, 16).replace("T", " "))} · ${esc(it.gift.status)}</div>
          <div class="what">🎁 для <b>${esc(it.gift.recipient_name)}</b> · ${esc(it.gift.occasion || "просто так")}</div>
          <div class="muted">${it.scenes.map((s) => `${esc(s.movie_title)} ⏱${esc(s.timecode_label)}`).join(" · ") || "—"}</div>
          ${it.replies.length ? `<div class="ans">ответов: ${it.replies.length} ${it.replies.map((r) => r.type === "scene" ? "🎬" : r.type === "reaction" ? esc(r.reaction || "") : "✉️").join(" ")}</div>` : ""}
          <div class="row" style="margin-top:6px"><button class="btn ghost small" onclick="location.hash='#/g/${it.gift.id}'">Открыть</button></div>
        </div>`).join("") || '<p class="muted">Пока пусто — создай первый подарок 🎁</p>'}
      </section>`;
      const relationships = await api(`/api/connections?telegram_id=${TG_ID}`);
      const connectionPanel = document.createElement("section");
      connectionPanel.className = "panel";
      connectionPanel.innerHTML = `<h3>Запросы на связь</h3><p class="muted">Связь появится после согласия обоих. Она не открывает ваши личные данные другому пользователю.</p>
        ${relationships.connections.map(c => `<article class="reply-item"><p>${esc(c.peer_name || c.peer_telegram_id)} · ${c.status === 'accepted' ? 'Подтверждено' : 'Ждёт подтверждения'}</p>
          ${c.incoming && c.status === 'pending' ? `<button class="btn ok connection-accept" data-id="${Number(c.id)}">Принять</button>` : ''}
          <button class="btn ghost connection-remove" data-id="${Number(c.id)}">${c.status === 'accepted' ? 'Разорвать связь' : 'Отклонить / отменить'}</button></article>`).join('') || '<p>Запросов нет</p>'}`;
      app.appendChild(connectionPanel);
      connectionPanel.querySelectorAll('.connection-accept').forEach(button => button.onclick = async () => {
        try {
          await api(`/api/connections/${button.dataset.id}/accept?telegram_id=${TG_ID}`, {method: 'POST'});
          renderTimeline();
        } catch (e) { toast(e.message); }
      });
      connectionPanel.querySelectorAll('.connection-remove').forEach(button => button.onclick = async () => {
        if (!window.confirm('Отменить эту связь?')) return;
        try {
          await api(`/api/connections/${button.dataset.id}?telegram_id=${TG_ID}`, {method: 'DELETE'});
          renderTimeline();
        } catch (e) { toast(e.message); }
      });
      const exportButton = document.createElement("button");
      exportButton.className = "btn ghost";
      exportButton.textContent = "Скачать мои персональные данные (JSON)";
      exportButton.onclick = async () => {
        try {
          const data = await api(`/api/users/${TG_ID}/export`);
          const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
          const link = document.createElement("a"); link.href = url; link.download = "scena-my-data.json";
          link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) { toast(e.message); }
      };
      app.appendChild(exportButton);
      const eraseButton = document.createElement("button");
      eraseButton.className = "btn ghost";
      eraseButton.textContent = "Удалить профиль и мои подарки";
      eraseButton.onclick = async () => {
        const confirmation = window.prompt("Это необратимо. Подарки станут недоступны. Финансовые записи и резервные копии хранятся по регламенту. Введите УДАЛИТЬ:");
        if (confirmation !== "УДАЛИТЬ") return;
        try {
          const result = await api('/api/account/delete', {method:'POST', body:{confirmation, telegram_id:TG_ID}});
          app.innerHTML = `<section class="panel" role="status"><h2>Профиль удалён</h2><p>${esc(result.message)}</p><p>Закройте Mini App. Повторный вход создаст пустой профиль.</p></section>`;
        } catch (e) { toast(e.message); }
      };
      app.appendChild(eraseButton);
      document.getElementById("btn-peer").onclick = async () => {
        const id = Number(document.getElementById("peer").value);
        if (!id) return;
        try { await api("/api/connections", { method: "POST", body: { peer_telegram_id: id, telegram_id: TG_ID } }); toast("Запрос отправлен — нужно подтверждение получателя"); renderTimeline(); }
        catch (e) { toast(e.message); }
      };
    } catch (e) {
      app.innerHTML = `<section class="view"><div class="panel">${esc(e.message)}</div></section>`;
    }
  }

  // ---------- каталог ----------
  let catOccasion = "", catMood = "";
  // выбор момента (таймкода) для выбранных сцен, если у сцены их несколько
  async function renderMomentPicker() {
    const box = document.getElementById("moment-picker");
    if (!box) return;
    const ids = [...state.selected];
    if (!ids.length) { box.innerHTML = ""; return; }
    let html = "";
    for (const id of ids) {
      try {
        const d = await api(`/api/scenes/${id}/moments`);
        if (d.moments.length > 1) {
          const cur = state.moments[id] || d.moments[0].timecode_label;
          html += `<div class="field" style="margin-top:6px"><span>⏱ Момент: ${esc(d.scene.movie_title)} (${esc(d.scene.movie_year)})</span>
            <div class="chips">${d.moments.map((m) => `<button class="chip${cur === m.timecode_label ? " active" : ""}" data-moment="${id}:${esc(m.timecode_label)}">⏱ ${esc(m.timecode_end_label ? m.timecode_label + "–" + m.timecode_end_label : m.timecode_label)}${m.moment_label ? " · " + esc(m.moment_label) : ""}</button>`).join("")}</div></div>`;
        }
      } catch (e) {}
    }
    box.innerHTML = html;
    box.querySelectorAll("[data-moment]").forEach((b) => (b.onclick = () => {
      const separator = b.dataset.moment.indexOf(":");
      const sid = b.dataset.moment.slice(0, separator), tc = b.dataset.moment.slice(separator + 1);
      state.moments[sid] = tc;
      box.querySelectorAll("[data-moment]").forEach((x) => x.classList.toggle("active", x.dataset.moment === b.dataset.moment));
    }));
  }

  async function renderCatalog(q) {
    // состояние фильтров (читаем из hash, чтобы фильтры были шар ссылкой)
    const st = {
      q: (q && q.get("q")) || "",
      occasion: (q && q.get("occasion")) || "",
      mood: (q && q.get("mood")) || "",
      genre: (q && q.get("genre")) || "",
      era: (q && q.get("era")) || "",
      age: (q && q.get("age")) || "",
      verified: (q && q.get("verified")) === "1",
      tc: (q && q.get("tc")) === "1",
      sort: (q && q.get("sort")) || "popular",
      list: (q && q.get("list")) || "",
      bookmarks: (q && q.get("bm")) === "1",
    };
    app.innerHTML = `<section class="view">
      <h2 class="h2">🎞️ Каталог сцен</h2>
      <input id="cat-q" class="search-input" placeholder="Найди по смыслу: «извиниться», «первая любовь», «смешное на 8 марта»…" value="${esc(st.q)}">
      <div class="chips" id="cat-lists">…</div>
      <div class="panel filters">
        <div class="field"><span>🎯 Повод</span><div class="chips" id="f-occ"></div></div>
        <div class="field"><span>💫 Настроение</span><div class="chips" id="f-mood"></div></div>
        <div class="row" style="gap:8px">
          <label class="field" style="flex:1"><span>Жанр</span><select id="f-genre" class="search-input"><option value="">все</option></select></label>
          <label class="field" style="flex:1"><span>Эпоха</span><select id="f-era" class="search-input"><option value="">все</option></select></label>
          <label class="field" style="flex:1"><span>Возраст</span><select id="f-age" class="search-input"><option value="">все</option></select></label>
          <label class="field" style="flex:1"><span>Сортировка</span>
            <select id="f-sort" class="search-input">
              <option value="popular">по популярности</option>
              <option value="year_desc">сначала новые</option>
              <option value="year_asc">сначала старые</option>
              <option value="title">по названию</option>
            </select></label>
        </div>
        <div class="row" style="gap:12px">
          <label class="chip ${st.verified ? "active" : ""}" id="f-verified">✓ только проверенные таймкоды</label>
          <label class="chip ${st.tc ? "active" : ""}" id="f-tc">⏱ с таймкодом</label>
          <label class="chip ${st.bookmarks ? "active" : ""}" id="f-bm">⭐ мои закладки</label>
          <button class="chip" id="f-reset">✕ сбросить</button>
          <span class="muted" id="cat-count"></span>
        </div>
      </div>
      <div id="cat-results" class="scene-list"></div>
      <div class="panel"><h3>➕ Предложить свою сцену</h3>
        <label class="field"><span>Фильм и год</span><input id="sg-title" placeholder="Название, 1999"></label>
        <label class="field"><span>Таймкод (если знаешь)</span><input id="sg-tc" placeholder="1:23:45"></label>
        <label class="field"><span>Поводы (через запятую)</span><input id="sg-occ" placeholder="признание, годовщина"></label>
        <label class="field"><span>Почему подходит (одно-два предложения)</span>
          <textarea id="sg-desc" rows="2"></textarea></label>
        <button id="sg-send" class="btn ghost">Отправить на модерацию</button>
      </div>
    </section>`;

    // мета каталога: категории с подсчётами из реальных данных
    let meta = { occasions: [], moods: [], genres: [], eras: [], ages: [], total: 0 };
    try { meta = await api("/api/scenes/meta"); } catch (e) {}
    document.getElementById("f-occ").innerHTML =
      '<button class="chip' + (st.occasion ? "" : " active") + '" data-v="">все</button>' +
      meta.occasions.map((o) => `<button class="chip${st.occasion === o.tag ? " active" : ""}" data-v="${esc(o.tag)}">${esc(o.tag)} ${o.count}</button>`).join("");
    document.getElementById("f-mood").innerHTML =
      '<button class="chip' + (st.mood ? "" : " active") + '" data-v="">все</button>' +
      meta.moods.map((m) => `<button class="chip${st.mood === m.tag ? " active" : ""}" data-v="${esc(m.tag)}">${esc(m.tag)} ${m.count}</button>`).join("");
    const fillSel = (id, items, cur) => {
      const s = document.getElementById(id);
      s.innerHTML = '<option value="">все</option>' + items.map((x) =>
        `<option value="${esc(x.tag)}" ${cur === x.tag ? "selected" : ""}>${esc(x.tag)} (${x.count})</option>`).join("");
    };
    fillSel("f-genre", meta.genres, st.genre);
    fillSel("f-era", meta.eras, st.era);
    fillSel("f-age", meta.ages, st.age);
    document.getElementById("f-sort").value = st.sort;

    // подборки
    try {
      const lists = (await api("/api/lists")).lists;
      const box = document.getElementById("cat-lists");
      box.innerHTML = '<button class="chip' + (st.list ? "" : " active") + '" data-list="">✨ все сцены</button>' +
        lists.map((l) => `<button class="chip${String(l.id) === st.list ? " active" : ""}" data-list="${l.id}">${esc(l.name)} (${l.count})</button>`).join("");
    } catch (e) {}

    const syncHash = () => {
      const p = new URLSearchParams();
      if (st.q) p.set("q", st.q);
      if (st.occasion) p.set("occasion", st.occasion);
      if (st.mood) p.set("mood", st.mood);
      if (st.genre) p.set("genre", st.genre);
      if (st.era) p.set("era", st.era);
      if (st.age) p.set("age", st.age);
      if (st.verified) p.set("verified", "1");
      if (st.tc) p.set("tc", "1");
      if (st.sort !== "popular") p.set("sort", st.sort);
      if (st.list) p.set("list", st.list);
      if (st.bookmarks) p.set("bm", "1");
      history.replaceState(null, "", "#/catalog" + (p.toString() ? "?" + p.toString() : ""));
    };

    const runSearch = async () => {
      syncHash();
      const box = document.getElementById("cat-results");
      box.innerHTML = '<p class="muted">Ищем…</p>';
      let scenes, total;
      if (st.list) {
        const d = await api("/api/lists/" + st.list);
        scenes = d.scenes; total = d.scenes.length;
      } else if (st.bookmarks) {
        await loadBookmarks();
        const bookmarked = await api(`/api/bookmarks?telegram_id=${TG_ID}`);
        scenes = bookmarked.scenes; total = scenes.length;
      } else if (st.q) {
        const d = await api("/api/scenes/semantic", { method: "POST", body: {
          query: st.q, occasion: st.occasion || null, limit: 50 } });
        scenes = d.scenes;
        // докрутим остальными фильтрами на клиенте
        if (st.mood) scenes = scenes.filter((s) => (s.mood_tags || []).map((t) => t.toLowerCase()).includes(st.mood.toLowerCase()));
        if (st.genre) scenes = scenes.filter((s) => (s.genre || "").toLowerCase() === st.genre.toLowerCase());
        if (st.era) scenes = scenes.filter((s) => (s.era || "").toLowerCase() === st.era.toLowerCase());
        if (st.age) scenes = scenes.filter((s) => s.age_rating === st.age);
        if (st.verified) scenes = scenes.filter((s) => s.verified);
        if (st.tc) scenes = scenes.filter((s) => s.timecode_label);
        if (st.sort === "year_desc") scenes.sort((a, b) => b.movie_year - a.movie_year);
        if (st.sort === "year_asc") scenes.sort((a, b) => a.movie_year - b.movie_year);
        if (st.sort === "title") scenes.sort((a, b) => a.movie_title.localeCompare(b.movie_title));
        scenes = scenes.slice(0, 50);
        total = scenes.length;
      } else {
        const d = await api("/api/scenes/search", { method: "POST", body: {
          occasion: st.occasion || null, mood: st.mood || null, genre: st.genre || null,
          era: st.era || null, age: st.age || null, verified_only: st.verified,
          has_timecode: st.tc, sort: st.sort, limit: 50 } });
        scenes = d.scenes; total = d.total;
      }
      document.getElementById("cat-count").textContent =
        `Найдено: ${total} из ${meta.total || ""}${meta.verified ? ` · ✓ проверено: ${meta.verified}` : ""}`;
      box.innerHTML = scenes.length ? scenes.map((s) => sceneCard(s)).join("")
        : '<div class="panel"><p class="muted">Ничего не нашлось 🤔</p><p class="muted">Попробуй снять часть фильтров или опиши по-другому в поиске сверху.</p></div>';
      attachSceneListClicks(box);
    };

    const wireChips = (id, key) => document.getElementById(id).addEventListener("click", (e) => {
      const v = e.target.dataset && e.target.dataset.v;
      if (v === undefined) return;
      st[key] = v;
      document.getElementById(id).querySelectorAll(".chip").forEach((c) =>
        c.classList.toggle("active", c.dataset.v === st[key]));
      runSearch();
    });
    wireChips("f-occ", "occasion");
    wireChips("f-mood", "mood");
    document.getElementById("cat-lists").addEventListener("click", (e) => {
      const v = e.target.dataset && e.target.dataset.list;
      if (v === undefined) return;
      st.list = v === "" ? "" : String(v);
      document.getElementById("cat-lists").querySelectorAll(".chip").forEach((c) =>
        c.classList.toggle("active", c.dataset.list === st.list));
      runSearch();
    });
    document.getElementById("f-genre").onchange = (e) => { st.genre = e.target.value; runSearch(); };
    document.getElementById("f-era").onchange = (e) => { st.era = e.target.value; runSearch(); };
    document.getElementById("f-age").onchange = (e) => { st.age = e.target.value; runSearch(); };
    document.getElementById("f-sort").onchange = (e) => { st.sort = e.target.value; runSearch(); };
    const toggle = (id, key) => document.getElementById(id).addEventListener("click", () => {
      st[key] = !st[key];
      document.getElementById(id).classList.toggle("active", st[key]);
      runSearch();
    });
    toggle("f-verified", "verified"); toggle("f-tc", "tc"); toggle("f-bm", "bookmarks");
    document.getElementById("f-reset").onclick = () => {
      Object.assign(st, { q: "", occasion: "", mood: "", genre: "", era: "", age: "",
        verified: false, tc: false, sort: "popular", list: "", bookmarks: false });
      document.getElementById("cat-q").value = "";
      renderCatalog(new URLSearchParams());
    };
    let deb;
    document.getElementById("cat-q").addEventListener("input", (e) => {
      clearTimeout(deb);
      deb = setTimeout(() => { st.q = e.target.value.trim(); runSearch(); }, 350);
    });
    document.getElementById("sg-send").onclick = async () => {
      try {
        await api("/api/scenes/suggest", { method: "POST", body: {
          movie_title: document.getElementById("sg-title").value,
          timecode_label: document.getElementById("sg-tc").value || null,
          occasion_tags: document.getElementById("sg-occ").value.split(",").map((s) => s.trim()).filter(Boolean),
          description: document.getElementById("sg-desc").value,
          telegram_id: TG_ID } });
        toast("Отправлено на модерацию 🎬"); document.getElementById("sg-desc").value = "";
      } catch (e) { toast(e.message); }
    };
    loadBookmarks().then(runSearch);
  }

  // ---------- даты ----------
  async function renderDates() {
    let dates = [];
    if (TG_ID) { try { dates = (await api(`/api/dates?telegram_id=${TG_ID}`)).dates; } catch (e) {} }
    app.innerHTML = `<section class="view">
      <h2 class="h2">📅 Важные даты</h2>
      <p class="muted">Напомним заранее — и предложим повод для сцены 🎬</p>
      <div class="panel">
        <label class="field"><span>Кому</span><input id="d-name" placeholder="Мама, любимый…"></label>
        <label class="field"><span>Дата</span><input id="d-date" type="date"></label>
        <label class="field"><span>Напомнить за (дней)</span><input id="d-remind" type="number" value="7" min="0" max="60"></label>
        <button id="d-add" class="btn primary" style="margin-top:8px">Добавить дату</button>
      </div>
      ${dates.map((d) => `<div class="tl-item"><div class="what">🎂 ${esc(d.name)} — ${esc(d.date)}</div>
        <div class="muted">напомним за ${d.remind_days} дн.</div></div>`).join("") || (TG_ID ? '<p class="muted">Пока нет дат</p>' : '<p class="muted">Открой из Telegram, чтобы добавлять даты</p>')}
    </section>`;
    document.getElementById("d-add").onclick = async () => {
      const v = document.getElementById("d-date").value; // YYYY-MM-DD
      if (!v || !TG_ID) return toast("Выбери дату (и войди через Telegram)");
      try {
        await api("/api/dates", { method: "POST", body: {
          name: document.getElementById("d-name").value || "Близкий",
          date: v.slice(5), remind_days: Number(document.getElementById("d-remind").value) || 7,
          telegram_id: TG_ID } });
        toast("Дата добавлена 📅"); renderDates();
      } catch (e) { toast(e.message); }
    };
  }

  // ---------- «ваш фильм» ----------
  async function renderFilm(q) {
    if (!TG_ID) { app.innerHTML = `<section class="view"><div class="panel">Открой из Telegram 💞</div></section>`; return; }
    try {
      const d = await api(`/api/film/${TG_ID}?year=${q.get("y") || ""}`);
      app.innerHTML = `<section class="view">
        <div class="film-cover">
          <div class="big">🎬</div>
          <h2>Ваш фильм ${d.year}</h2>
          <div class="sub">${esc(d.user.first_name || "Вы")} · ${d.gifts} подарков · ${d.scenes} сцен · ${d.replies} ответов</div>
        </div>
        <div class="panel"><h3>📊 Статистика</h3>
          <div class="stat-grid">
            <div class="stat"><div class="v">${d.gifts}</div><div class="l">подарков</div></div>
            <div class="stat"><div class="v">${d.scenes}</div><div class="l">сцен</div></div>
            <div class="stat"><div class="v">${d.replies}</div><div class="l">ответов</div></div>
          </div>
          ${d.first ? `<p class="muted" style="margin-top:8px">Первый момент: ${esc(d.first.slice(0, 10))} · последний: ${esc((d.last || "").slice(0, 10))}</p>` : ""}
        </div>
        ${d.top_movies.length ? `<div class="panel"><h3>🎞️ Топ фильмов вашей истории</h3>
          ${d.top_movies.map((m) => `<div class="tl-item" style="margin-top:6px">${esc(m.title)} — сцен: ${m.count}</div>`).join("")}</div>` : ""}
        <button class="btn primary" id="film-share">📤 Поделиться «вашим фильмом»</button>
        <button class="btn ghost" id="film-export">📥 Экспорт фильма (HTML → PDF/книга)</button>
        <button class="btn ghost" id="film-premium">⭐ Премиум-версия (экспорт, 499 ₽/год)</button>
      </section>`;
      document.getElementById("film-share").onclick = () =>
        openLink("https://t.me/share/url?url=" + encodeURIComponent(d.share_url) +
          "&text=" + encodeURIComponent(`Наш «ваш фильм» ${d.year} 🎬: ${d.gifts} подарков и ${d.scenes} сцен из кино!`));
      document.getElementById("film-export").onclick = async () => {
        const headers = TG && TG.initData ? { "X-Telegram-Init-Data": TG.initData } : {};
        const res = await fetch(`/api/film/${TG_ID}/export?year=${d.year}`, { headers });
        if (res.status === 402) {
          toast("Экспорт — в премиуме ⭐ (кнопка ниже)");
          const b = document.getElementById("film-premium");
          if (b) b.scrollIntoView({ behavior: "smooth" });
          return;
        }
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `vash-film-${d.year}.html`;
        a.click();
        toast("Фильм экспортирован 📥 (открой файл и сохрани как PDF)");
      };
      document.getElementById("film-premium").onclick = async () => {
        try {
          const p = await api("/api/payments/create", { method: "POST", body: { kind: "premium_film", telegram_id: TG_ID } });
          location.hash = "#/pay/" + p.payment_id;
        } catch (e) { toast(e.message); }
      };
    } catch (e) { app.innerHTML = `<section class="view"><div class="panel">${esc(e.message)}</div></section>`; }
  }

  // ---------- букеты ----------
  async function renderBouquets(q) {
    const g = q.get("g");
    let city = "Москва";
    if (g) {
      try {
        const gift = await api("/api/gifts/" + g);
        if (!gift.locked && gift.gift.recipient_city) city = gift.gift.recipient_city;
      } catch (e) { /* оставляем дефолт */ }
    }
    app.innerHTML = `<section class="view">
      <h2 class="h2">🌷 Добавить букет</h2>
      <p class="muted">Заказ через партнёрскую ссылку — комиссия студии нам, вам — удобно 😉</p>
      <div class="row">${CITIES.map((c) => `<button class="chip ${c === city ? "active" : ""}" data-city="${c}">${c}</button>`).join("")}</div>
      <div id="bq-list"></div>
    </section>`;
    const load = async () => {
      const d = await api(`/api/bouquets?city=${encodeURIComponent(city)}`);
      document.getElementById("bq-list").innerHTML = d.bouquets.map((b) => `
        <div class="bouquet">
          <div class="emoji">💐</div>
          <div class="info"><div class="t">${esc(b.title)}</div>
            <div class="p">${b.price_rub} ₽</div><div class="s">${esc(b.shop_name || "партнёр Flowwow")}</div></div>
          <button class="btn primary small bq-order" data-id="${b.id}" data-price="${b.price_rub}">Заказать</button>
        </div>`).join("") || '<p class="muted">В этом городе пока нет витрины</p>';
      document.querySelectorAll(".bq-order").forEach((btn) => (btn.onclick = async () => {
        if (!g) return toast("Открой букеты со страницы подарка");
        try {
          const o = await api(`/api/gifts/${g}/order-bouquet`, { method: "POST", body: { bouquet_id: Number(btn.dataset.id), amount: Number(btn.dataset.price) } });
          toast(`Заказ оформлен на ${o.commission_rub} ₽ комиссии — открываем Flowwow…`);
          openLink(o.go_url);
        } catch (e) { toast(e.message); }
      }));
    };
    app.querySelectorAll("[data-city]").forEach((c) => (c.onclick = () => {
      city = c.dataset.city;
      app.querySelectorAll("[data-city]").forEach((x) => x.classList.toggle("active", x === c));
      load();
    }));
    load();
  }

  // ---------- кабинет студии ----------
  async function renderShop(q) {
    const id = q.get("id");
    if (!id) {
      let owned = [];
      if (TG_ID) {
        try { owned = (await api(`/api/shops/mine?telegram_id=${TG_ID}`)).shops; }
        catch (e) { toast(e.message); }
      }
      app.innerHTML = `<section class="view"><h2>Мои студии</h2>
        ${owned.map(shop => `<button class="btn ghost studio-open" data-id="${Number(shop.id)}">${esc(shop.name)} · ${esc(shop.city)}</button>`).join('') || '<p>Студии пока не назначены. Обратитесь в поддержку для подключения.</p>'}
        <label class="field">ID студии для оператора<input id="studio-id" inputmode="numeric"></label>
        <button class="btn ghost" id="studio-manual">Открыть по ID</button></section>`;
      app.querySelectorAll('.studio-open').forEach(button => button.onclick = () => { location.hash = '#/shop?id=' + button.dataset.id; });
      document.getElementById('studio-manual').onclick = () => {
        const value = Number(document.getElementById('studio-id').value);
        if (Number.isInteger(value) && value > 0) location.hash = '#/shop?id=' + value;
      };
      return;
    }
    try {
      const d = await adminApi(`/api/shops/${encodeURIComponent(id)}/stats`);
      app.innerHTML = `<section class="view">
        <h2 class="h2">🏪 ${esc(d.shop.name)}</h2>
        <p class="muted">${esc(d.shop.city || "")} · комиссия ${d.shop.commission_pct}% · подписка: ${d.shop.subscription}</p>
        <div class="panel"><h3>📊 Статистика</h3>
          <div class="stat-grid">
            <div class="stat"><div class="v">${d.cards_used}/${d.cards_total}</div><div class="l">карточек использовано</div></div>
            <div class="stat"><div class="v">${d.gifts_created}</div><div class="l">подарков</div></div>
            <div class="stat"><div class="v">${d.commission_rub} ₽</div><div class="l">комиссия (${d.orders} зак.)</div></div>
          </div></div>
        <div class="panel"><h3>🖨️ Заказать QR-карточки</h3>
          <div class="row"><input id="card-count" class="search-input" style="max-width:120px" type="number" value="100" min="10" max="10000">
          <button id="btn-cards-free" class="btn ghost small" ${localStorage.getItem("scena_admin_token") ? "" : "hidden"}>Выпустить (оператор)</button>
          <button id="btn-cards-buy" class="btn primary small">Купить с оплатой 💳</button></div>
          <div id="card-out" class="muted" style="margin-top:8px;word-break:break-all"></div>
          <p class="muted" style="margin-top:6px">Печатный макет карточки A6: после выпуска открой
            <a id="card-preview-link" href="#">⬇️ /api/cards/…/preview</a> (с QR) — для типографии</p></div>
        <div class="panel"><h3>🧾 Заказы по партнёрским ссылкам (агентская комиссия)</h3>
          <div class="row">
            <input id="oc-id" class="search-input" style="max-width:170px" placeholder="id заказа Flowwow">
            <input id="oc-amount" class="search-input" style="max-width:110px" type="number" placeholder="сумма, ₽">
            <button id="oc-confirm" class="btn ok small">Подтвердить</button>
          </div>
          <div id="oc-list" class="muted" style="margin-top:8px">Загружаем…</div>
        </div>
      </section>`;
      document.getElementById("btn-cards-free").onclick = async () => {
        const n = Number(document.getElementById("card-count").value) || 100;
        const r = await adminApi(`/api/shops/${id}/cards`, { method: "POST", body: { count: n } });
        document.getElementById("card-out").innerHTML =
          `Выпущено ${r.count} карточек. Первые ссылки:<br>` +
          r.urls.map((u) => `<a class="gift-link" href="${esc(u)}">${esc(u)}</a>`).join("");
        const code = (r.urls[0] || "").split("/").pop();
        if (code) {
          const pl = document.getElementById("card-preview-link");
          pl.href = `/api/cards/${encodeURIComponent(code)}/preview`;
          pl.textContent = `⬇️ макет первой карточки (${code})`;
        }
        toast("Карточки выпущены 🖨️");
      };
      document.getElementById("btn-cards-buy").onclick = async () => {
        const n = Number(document.getElementById("card-count").value) || 100;
        try {
          const p = await api(`/api/shops/${id}/buy-cards`, { method: "POST", body: { count: n } });
          location.hash = "#/pay/" + p.payment_id;
        } catch (e) { toast(e.message); }
      };
      const loadOrders = async () => {
        try {
          const d = await adminApi(`/api/shops/${id}/orders`);
          document.getElementById("oc-list").innerHTML = d.orders.length
            ? d.orders.map((o) => `<div class="tl-item" style="margin-top:6px">
                ${esc(o.flowwow_order_id || "—")} · ${o.amount} ₽ · <b>${esc(o.status)}</b> · комиссия ${o.commission_rub} ₽
                ${o.ref_code ? `<br><span class="muted">ref: ${esc(o.ref_code)}</span>` : ""}</div>`).join("")
            : "Пока нет заказов";
        } catch (e) { document.getElementById("oc-list").textContent = "Нет доступа (нужен admin token)"; }
      };
      document.getElementById("oc-confirm").onclick = async () => {
        const oid = document.getElementById("oc-id").value.trim();
        const amt = Number(document.getElementById("oc-amount").value) || null;
        if (!oid) return toast("Введи id заказа Flowwow");
        try {
          const r = await adminApi(`/api/shops/${id}/orders/confirm`, {
            method: "POST", body: { flowwow_order_id: oid, amount: amt } });
          toast(`Заказ подтверждён: комиссия ${r.commission_rub} ₽ ✅`); loadOrders();
        } catch (e) { toast(e.message); }
      };
      loadOrders();
    } catch (e) { app.innerHTML = `<section class="view"><div class="panel">${esc(e.message)}</div></section>`; }
  }

  // ---------- оплата ----------
  async function renderPay(pid) {
    let p;
    try { p = await api("/api/payments/" + pid); }
    catch (e) { app.innerHTML = `<section class="view"><div class="panel">${esc(e.message)}</div></section>`; return; }
    const kindLabel = { card_pack: "Пачка QR-карточек", subscription: "Подписка студии (30 дней)", premium_film: "Премиум «ваш фильм»" }[p.kind] || p.kind;
    app.innerHTML = `<section class="view">
      <h2 class="h2">💳 Оплата</h2>
      <div class="panel" style="text-align:center">
        <div style="font-size:40px">🎬</div>
        <h3 style="margin:8px 0">${esc(kindLabel)}</h3>
        <div style="font-size:28px;font-weight:800;color:var(--accent)">${p.amount_rub} ₽</div>
        <p class="muted" style="margin:8px 0">Статус: <b>${p.status === "paid" ? "✅ оплачено" : esc(p.status)}</b> · провайдер: ${esc(p.provider)}</p>
        ${p.status === "pending"
          ? `<button id="btn-pay" class="btn primary">${p.provider === "dev" ? "Оплатить (dev)" : "Перейти к оплате"}</button>`
          : p.status === "paid" ? `<p class="muted">Эффект применён ✅</p>`
          : (p.status === "creating" || p.status.startsWith("verifying_")) ? `<p class="muted">Платёж обрабатывается. Обновите статус через несколько секунд.</p><button id="pay-refresh" class="btn ghost">Обновить</button>`
          : `<p class="muted">Платёж не завершён.</p>`}
        <div class="row" style="justify-content:center;margin-top:10px">
          <button class="btn ghost small" onclick="history.back()">Назад</button>
        </div>
      </div></section>`;
    const refresh = document.getElementById("pay-refresh");
    if (refresh) refresh.onclick = () => renderPay(pid);
    const btn = document.getElementById("btn-pay");
    if (btn) btn.onclick = async () => {
      if (p.provider === "dev") {
        try {
          await api(`/api/payments/${pid}/confirm-dev`, { method: "POST" });
          toast("Оплачено ✅"); renderPay(pid);
        } catch (e) { toast(e.message); }
      } else {
        openLink(p.confirmation_url);
      }
    };
  }

  // ---------- админка ----------
  function adminApi(path, opts = {}) {
    const token = localStorage.getItem("scena_admin_token") || "";
    return api(path, { ...opts, headers: { "X-Admin-Token": token } });
  }

  async function renderAdmin() {
    const token = localStorage.getItem("scena_admin_token") || "";
    app.innerHTML = `<section class="view">
      <h2 class="h2">🛠 Админка</h2>
      <div class="panel">
        <label class="field"><span>Admin token</span>
          <div class="row"><input id="adm-token" class="search-input" value="${esc(token)}" placeholder="X-Admin-Token">
          <button id="adm-save" class="btn ghost small">Сохранить</button></div></label>
      </div>
      <section class="panel"><h3>Назначить владельца студии</h3>
        <p class="muted">Владелец должен сначала войти в Mini App. Только оператор может изменить назначение.</p>
        <label class="field">ID студии<input id="owner-shop" type="number" min="1"></label>
        <label class="field">Telegram ID владельца<input id="owner-tg" type="number" min="1"></label>
        <button class="btn ghost" id="owner-assign">Назначить владельца</button></section>
      <button id="adm-load" class="btn primary">Загрузить</button>
      <div id="adm-body"><p class="muted">Сохрани токен и нажми «Загрузить»…</p></div>
    </section>`;
    document.getElementById('owner-assign').onclick = async () => {
      const shopId = Number(document.getElementById('owner-shop').value);
      const telegramId = Number(document.getElementById('owner-tg').value);
      if (!Number.isSafeInteger(shopId) || shopId < 1 || !Number.isSafeInteger(telegramId) || telegramId < 1) return toast('Укажите корректные ID');
      if (!window.confirm('Назначить нового владельца? Предыдущий потеряет доступ.')) return;
      try {
        await adminApi(`/api/admin/shops/${shopId}/owner`, {method:'POST', body:{telegram_id:telegramId}});
        toast('Владелец назначен');
      } catch (e) { toast(e.message); }
    };
    document.getElementById("adm-save").onclick = () => {
      localStorage.setItem("scena_admin_token", document.getElementById("adm-token").value.trim());
      toast("Токен сохранён");
    };
    document.getElementById("adm-load").onclick = async () => {
      const body = document.getElementById("adm-body");
      try {
        const ov = await adminApi("/api/admin/overview");
        const reports = await adminApi("/api/admin/reports");
        const metrics = await adminApi('/api/admin/metrics');
        const failedNotifications = await adminApi('/api/admin/notifications/failed');
        const auditLog = await adminApi('/api/admin/audit');
        const analytics = await adminApi("/api/admin/analytics");
        const drafts = await adminApi("/api/admin/scenes?status=draft");
        const unv = await adminApi("/api/admin/scenes?status=approved&verified=0");
        const tcs = await adminApi("/api/admin/timecodes?status=pending");
        const lib = await adminApi("/api/admin/timecode-library");
        const libCat = lib && lib.catalog ? lib.catalog : {};
        const libCand = lib && lib.candidates ? lib.candidates : {};
        const libMan = lib && lib.manifests ? lib.manifests : {};
        const libMod = lib && lib.moderation ? lib.moderation : {};
        const libTargets = Array.isArray(libMod.targets) ? libMod.targets : [];
        const libFull = libMan.full || {};
        const libStatus = libCand.by_status || {};
        body.innerHTML = `<section class="panel"><h3>📚 Библиотека таймкодов</h3>
          <p>Каталог: ${Number(libCat.total ?? 0)} сцен · IMDb ID: ${Number(libCat.with_imdb ?? 0)} · с реальным таймкодом: ${Number(libCat.with_timecode ?? 0)} · проверено вручную: ${Number(libCat.verified ?? 0)}</p>
          <p>Кандидаты: pending ${Number(libStatus.pending ?? 0)}, approved ${Number(libStatus.approved ?? 0)}, rejected ${Number(libStatus.rejected ?? 0)} · сцен в очереди: ${Number(libCand.scenes_with_candidates ?? 0)}</p>
          <p class="muted">Пилот: ${Number(libMan.pilot_scenes ?? 0)} сцен · полный манифест: ${Number(libFull.matched_scenes ?? 0)} из ${Number(libFull.catalog_scenes ?? 0)} · файлов субтитров: ${Number(libFull.subtitle_files ?? 0)} · сомнительных: ${Number(libMan.ambiguous_count ?? 0)} · исключено ложных: ${Number(libMan.excluded_count ?? 0)}</p>
          <p class="muted">Легальные бесплатные версии: ${Number(libMod.scenes_with_official_deep ?? 0)} сцен с deep link на момент · ${Number(libMod.official_videos ?? 0)} ссылок · сверка издания обязательна</p>
          ${((lib.sources && lib.sources.registry) ? lib.sources.registry : []).map(s =>
            `<p class="muted">${esc(s.id)}: ${Number(s.count ?? 0)} файлов · ${esc((s.license || '').slice(0, 90))}</p>`).join('')}
          <p class="muted">${esc((lib && lib.note) || 'Ручная сверка обязательна.')}</p>
          </section><section class="panel"><h3>🎬 Легальные полные версии для ручного поиска момента</h3>
          <p class="muted">Здесь фильмы, для которых есть официальная бесплатная полная версия (Мосфильм и др.), но GitHub-SRT часто ничего не нашёл. Открой фильм, найди момент сам и добавь кандидата в очередь — он останется pending до «✓ Подтвердить». Пиратские зеркала не используются.</p>
          ${libTargets.map(t => `<article class="reply-item md-target" data-title="${esc(t.movie_title)}" data-year="${Number(t.movie_year)}" data-url="${esc(t.url || "")}" data-source="${esc(t.url || "")}" data-imdb="${esc(t.imdb_id || "")}">
            <p><b>${esc(t.movie_title)}</b> (${Number(t.movie_year)})${t.imdb_id ? ` · ${esc(t.imdb_id)}` : ""} · ${esc(t.provider || "официальный источник")} · в очереди: ${Number(t.pending_candidates ?? 0)}</p>
            <p class="muted">${esc((t.version_hint || "").slice(0, 140))}</p>
            <div class="row" style="margin-bottom:6px">
              <button class="btn ghost small md-target-open">▶ Открыть фильм</button>
              <a class="muted" href="${esc(t.evidence_url || "#")}" target="_blank" rel="noopener noreferrer">источник ссылки</a>
            </div>
            <div class="row" style="margin-top:4px">
              <input class="search-input md-start" style="max-width:95px" placeholder="начало *" value="">
              <input class="search-input md-end" style="max-width:95px" placeholder="конец" value="">
              <input class="search-input md-version" style="max-width:150px" placeholder="версия издания *" value="">
              <input class="search-input md-label" style="max-width:220px" placeholder="метка момента нашими словами" value="">
              <button class="btn ok small md-add">＋ Добавить момент</button>
            </div>
          </article>`).join("") || '<p class="muted">Официальные бесплатные версии пока не заведены.</p>'}
          </section><section class="panel"><h3>Состояние фоновых задач</h3>
          <p>Worker: ${esc(metrics.worker.status)} · без heartbeat: ${esc(metrics.worker.age_seconds ?? '—')} сек.</p>
          <p>В очереди: ${Number(metrics.pending_notifications)} · окончательных ошибок: ${Number(metrics.failed_notifications)}</p>
          ${failedNotifications.notifications.map(n => `<article class="reply-item"><p>#${Number(n.id)} · ${esc(n.kind)} · попыток: ${Number(n.attempts)}</p>
            <button class="btn ghost notification-retry" data-id="${Number(n.id)}">Вернуть в очередь</button></article>`).join('')}
          </section><section class="panel"><h3>Журнал действий</h3><p class="muted">Последние 50 записей. При общем admin-токене отдельные операторы не различаются.</p>
          ${auditLog.entries.map(entry => `<p>${esc(entry.created_at)} · ${esc(entry.action)} · ${esc(entry.actor)} · объект ${esc(entry.resource)}</p>`).join('') || '<p>Записей пока нет</p>'}</section>
          <div class="panel"><h3>События (не уникальные пользователи)</h3>
          ${Object.entries(analytics.funnel).map(([kind, count]) => `<p>${esc(kind)}: ${Number(count)}</p>`).join("")}</div>
          <div class="panel"><h3>Жалобы</h3>
          ${reports.reports.filter(r => r.status === "open").map(r => `<article class="reply-item">
            <p>Подарок ${esc(r.gift_id)}: ${esc(r.reason)}</p>
            <button class="btn ghost close-report" data-id="${Number(r.id)}">Закрыть жалобу</button></article>`).join("") || '<p>Открытых жалоб нет</p>'}</div>
          <div class="panel"><h3>📊 Обзор</h3>
            <div class="stat-grid">
              <div class="stat"><div class="v">${ov.users}</div><div class="l">юзеров</div></div>
              <div class="stat"><div class="v">${ov.scenes}</div><div class="l">сцен</div></div>
              <div class="stat"><div class="v">${ov.gifts}</div><div class="l">подарков</div></div>
              <div class="stat"><div class="v">${ov.shops}</div><div class="l">студий</div></div>
              <div class="stat"><div class="v">${ov.cards}</div><div class="l">карточек</div></div>
              <div class="stat"><div class="v">${ov.revenue_rub} ₽</div><div class="l">выручка</div></div>
            </div></div>
          <div class="panel"><h3>🧪 Модерация сцен (draft: ${drafts.scenes.length})</h3>
            ${drafts.scenes.map((s) => `<div class="tl-item" style="margin-top:6px">
              <b>${esc(s.movie_title)}</b> ${s.movie_year || ""} · ⏱ ${esc(s.timecode_label || "?")}<br>
              <span class="muted">${esc(s.description)}</span><br>
              <div class="row" style="margin-top:6px">
                <button class="btn ok small adm-approve" data-id="${s.id}">Approve</button>
                <button class="btn ghost small adm-reject" data-id="${s.id}">Reject</button>
              </div></div>`).join("") || '<p class="muted">Пусто</p>'}
          </div>
          <div class="panel"><h3>⏱ Сверка таймкодов (не проверено: ${unv.scenes.length})</h3>
            <p class="muted">Открой сцену в легальном плеере, найди момент, заполни поля и сохрани — сцена станет verified.</p>
            ${unv.scenes.slice(0, 30).map((s) => `<div class="tl-item" style="margin-top:6px" data-vscene="${s.id}">
              <b>${esc(s.movie_title)}</b> (${s.movie_year || "?"}) — сейчас ⏱ ${esc(s.timecode_label || "0:00:00")}<br>
              <div class="row" style="margin-top:6px">
                <select class="search-input v-service" style="max-width:110px">
                  ${["kinopoisk", "ivi", "okko", "youtube"].map((x) => `<option ${x === s.service ? "selected" : ""}>${x}</option>`).join("")}
                </select>
                <input class="search-input v-tc" style="max-width:90px" placeholder="1:23:45" value="${esc(s.timecode_label || "")}">
                <input class="search-input v-sec" style="max-width:80px" type="number" placeholder="сек" value="${s.timecode_seconds ?? ""}">
                <input class="search-input v-ver" style="max-width:130px" placeholder="версия издания" value="${esc(s.version_note || "")}">
                <input class="search-input v-url" placeholder="watch_url (deep link)" value="${esc(s.watch_url || "")}">
                <button class="btn ok small v-save" data-id="${s.id}">✓ verified</button>
              </div></div>`).join("") || '<p class="muted">Все проверены ✅</p>'}
          </div>
          <div class="panel"><h3>📥 Таймкоды из библиотек субтитров (ожидают проверки: ${tcs.candidates.length})</h3>
            <div class="row" style="margin-bottom:8px">
              <button id="tc-bulk" class="btn ghost small" disabled>⚡ Авто-одобрение отключено</button>
              <span class="muted">verified=1 ставится только вручную: открой момент в легальном плеере, укажи версию издания и нажми «✓ Подтвердить»</span>
            </div>
            <p class="muted">Кандидаты получены из SRT-зеркал OpenSubtitles на GitHub: сохранены только время, sha1, источник и лицензия — текста реплик нет. Открой фильм в легальном плеере, найди описанный ниже момент, при необходимости поправь начало/конец и нажми «✓ Подтвердить». Только после этого сцена получит verified=1.</p>
            ${tcs.candidates.map((c) => {
              const range = c.timecode_end_label ? `${c.timecode_label}–${c.timecode_end_label}` : (c.timecode_label || "—");
              const src = c.source || "";
              const md = (c.moderation_links || []).filter(l => l.url).slice(0, 5);
              const local = (c.moderation_links || []).find(l => l.kind === 'local');
              const hasOfficial = (c.moderation_links || []).some(l => l.kind === 'official_deep');
              return `<div class="tl-item" style="margin-top:8px" data-title="${esc(c.movie_title)}" data-year="${esc(c.movie_year ?? "")}">
                <b>${esc(c.movie_title)}</b> (${esc(c.movie_year ?? "?")})${c.imdb_id ? ` · IMDb ${esc(c.imdb_id)}` : ""} · предлагается ⏱ <b>${esc(range)}</b><br>
                <span class="muted">Наш момент: ${esc(c.description || "—")}${c.why_fits ? ` — ${esc(c.why_fits)}` : ""}</span><br>
                <span class="muted">Метка: ${esc(c.moment_label || "без метки")} · строка #${c.line_index ?? "?"} · консенсус: ${c.consensus ?? 1} · confidence: ${c.confidence ?? 0}${c.auto ? " · ⚡auto" : ""}</span><br>
                <span class="muted">Источник: <a href="${esc(src || "#")}" target="_blank" rel="noopener noreferrer">${esc(src.slice(0, 90) || "—")}</a> · лицензия: ${esc((c.source_license || "").slice(0, 90))}</span>
                <div class="row" style="margin-top:6px">
                  <select class="search-input tc-service" style="max-width:110px">
                    ${["kinopoisk", "ivi", "okko", "youtube"].map((x) => `<option ${x === (c.service || "kinopoisk") ? "selected" : ""}>${x}</option>`).join("")}
                  </select>
                  <button class="btn ghost small tc-open">🔍 Найти фильм в плеере</button>
                  <button class="btn ghost small tc-copy" data-tc="${esc(range)}">⏱ Копировать время</button>
                </div>
                <div class="row" style="margin-top:6px">
                  ${md.map(l => `<button class="btn ghost small md-link" data-url="${esc(l.url)}" title="${esc(l.note || "")}">${esc(l.label)}</button>`).join("")}
                  ${local ? `<button class="btn ghost small md-copy" data-cmd="${esc(local.commands[0].command)}">⏱ Локально (mpv)</button>` : ""}
                </div>
                ${hasOfficial ? '<p class="muted">⚠️ Официальная бесплатная версия найдена, но издание/реставрация может отличаться — обязательно заполни «версия издания *».</p>' : ""}
                <div class="row" style="margin-top:6px">
                  <input class="search-input tc-start" style="max-width:95px" placeholder="начало 1:23:45" value="${esc(c.timecode_label || "")}">
                  <input class="search-input tc-end" style="max-width:95px" placeholder="конец 1:24:10" value="${esc(c.timecode_end_label || "")}">
                  <input class="search-input tc-version" style="max-width:140px" placeholder="версия издания *" value="" required>
                  <input class="search-input tc-url" placeholder="watch_url (deep link, если есть)" value="">
                </div>
                <div class="row" style="margin-top:6px">
                  <input class="search-input tc-label" style="max-width:280px" placeholder="метка момента НАШИМИ словами" value="${esc(c.moment_label || "")}">
                  <button class="btn ok small tc-approve" data-id="${c.id}">✓ Подтвердить</button>
                  <button class="btn ghost small tc-reject" data-id="${c.id}">✗ Отклонить</button>
                </div></div>`;
            }).join("") || '<p class="muted">Очередь пуста ✅</p>'}
          </div>`;
        body.querySelectorAll('.notification-retry').forEach(button => button.onclick = async () => {
          if (!window.confirm('Вернуть уведомление в очередь? Сначала устраните причину ошибки. Если Telegram уже принял сообщение до сбоя, возможен дубль.')) return;
          button.disabled = true;
          try {
            await adminApi(`/api/admin/notifications/${button.dataset.id}/retry`, {method:'POST'});
            document.getElementById('adm-load').click();
          } catch (e) { toast(e.message); button.disabled = false; }
        });
        body.querySelectorAll(".close-report").forEach(button => {
          button.onclick = async () => {
            try {
              await adminApi(`/api/admin/reports/${button.dataset.id}/close`, {method: "POST"});
              document.getElementById("adm-load").click();
            } catch (e) { toast(e.message); }
          };
        });
        body.querySelectorAll(".adm-approve").forEach((b) => (b.onclick = async () => {
          await adminApi(`/api/admin/scenes/${b.dataset.id}/status`, { method: "POST", body: { status: "approved" } });
          toast("Approved ✅"); document.getElementById("adm-load").click();
        }));
        body.querySelectorAll(".adm-reject").forEach((b) => (b.onclick = async () => {
          await adminApi(`/api/admin/scenes/${b.dataset.id}/status`, { method: "POST", body: { status: "rejected" } });
          toast("Rejected"); document.getElementById("adm-load").click();
        }));
        body.querySelectorAll(".tc-open").forEach((b) => (b.onclick = () => {
          const box = b.closest(".tl-item");
          const service = box.querySelector(".tc-service").value;
          const q = `${box.dataset.title || ""} ${box.dataset.year || ""}`.trim();
          const make = PLAYER_SEARCH[service] || PLAYER_SEARCH.kinopoisk;
          openLink(make(q));
        }));
        body.querySelectorAll(".tc-copy").forEach((b) => (b.onclick = async () => {
          try { await navigator.clipboard.writeText(b.dataset.tc || ""); toast("Время скопировано ⏱"); }
          catch (e) { toast("Скопируй время вручную"); }
        }));
        body.querySelectorAll(".md-link").forEach((b) => (b.onclick = () => openLink(b.dataset.url)));
        body.querySelectorAll(".md-copy").forEach((b) => (b.onclick = async () => {
          try { await navigator.clipboard.writeText(b.dataset.cmd || ""); toast("Команда скопирована: замени путь к файлу ⏱"); }
          catch (e) { toast("Скопируй команду вручную"); }
        }));
        body.querySelectorAll(".md-target-open").forEach((b) => (b.onclick = () => {
          const box = b.closest(".md-target");
          if (box && box.dataset.url) openLink(box.dataset.url);
        }));
        body.querySelectorAll(".md-add").forEach((b) => (b.onclick = async () => {
          const box = b.closest(".md-target");
          if (!box) return;
          const payload = {
            movie_title: box.dataset.title,
            movie_year: Number(box.dataset.year),
            timecode_label: (box.querySelector(".md-start").value || "").trim(),
            timecode_end_label: (box.querySelector(".md-end").value || "").trim() || null,
            moment_label: (box.querySelector(".md-label").value || "").trim() || null,
            version_note: (box.querySelector(".md-version").value || "").trim(),
            source_url: box.dataset.source || null,
          };
          if (!payload.timecode_label || !payload.version_note) {
            toast("Укажи начало и версию издания"); return;
          }
          try {
            await adminApi("/api/admin/timecodes/manual", { method: "POST", body: payload });
            toast("Момент добавлен в pending-очередь ✅");
            document.getElementById("adm-load").click();
          } catch (e) { toast(e.message); }
        }));
        body.querySelectorAll(".tc-approve").forEach((b) => (b.onclick = async () => {
          const box = b.closest(".tl-item");
          const version = (box.querySelector(".tc-version").value || "").trim();
          if (!version) { toast("Укажи версию издания перед подтверждением"); return; }
          try {
            await adminApi(`/api/admin/timecodes/${b.dataset.id}/review`, { method: "POST", body: {
              action: "approve",
              service: box.querySelector(".tc-service").value || null,
              timecode_label: box.querySelector(".tc-start").value || null,
              timecode_end_label: box.querySelector(".tc-end").value || null,
              version_note: version,
              watch_url: box.querySelector(".tc-url").value || null,
              moment_label: box.querySelector(".tc-label").value || null,
            } });
            toast("Таймкод подтверждён ✅ verified=1"); document.getElementById("adm-load").click();
          } catch (e) { toast(e.message); }
        }));
        body.querySelectorAll(".tc-reject").forEach((b) => (b.onclick = async () => {
          await adminApi(`/api/admin/timecodes/${b.dataset.id}/review`, { method: "POST", body: { action: "reject" } });
          toast("Отклонено"); document.getElementById("adm-load").click();
        }));
        body.querySelectorAll(".v-save").forEach((b) => (b.onclick = async () => {
          const box = b.closest("[data-vscene]");
          try {
            await adminApi(`/api/admin/scenes/${b.dataset.id}/verify`, { method: "POST", body: {
              service: box.querySelector(".v-service").value,
              timecode_label: box.querySelector(".v-tc").value || null,
              timecode_seconds: Number(box.querySelector(".v-sec").value) || null,
              version_note: box.querySelector(".v-ver").value || null,
              watch_url: box.querySelector(".v-url").value || null,
            } });
            toast("Таймкод проверен ✓"); document.getElementById("adm-load").click();
          } catch (e) { toast(e.message); }
        }));
      } catch (e) { body.innerHTML = `<p class="muted">Ошибка: ${esc(e.message)}</p>`; }
    };
    if (token) document.getElementById("adm-load").click();
  }

  // ---------- конфетти при первом открытии подарка ----------
  function confetti() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const c = document.createElement("canvas");
    c.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9999";
    document.body.appendChild(c);
    const ctx = c.getContext("2d");
    c.width = innerWidth; c.height = innerHeight;
    const colors = ["#ff5c8a", "#7c5cff", "#ffd166", "#4cd7d0", "#fff"];
    const parts = Array.from({ length: 160 }, () => ({
      x: Math.random() * c.width, y: -20 - Math.random() * c.height * 0.5,
      w: 6 + Math.random() * 6, h: 8 + Math.random() * 8,
      vy: 2 + Math.random() * 3.5, vx: -1.5 + Math.random() * 3,
      rot: Math.random() * Math.PI, vr: -0.1 + Math.random() * 0.2,
      col: colors[(Math.random() * colors.length) | 0],
    }));
    let frames = 0;
    (function anim() {
      ctx.clearRect(0, 0, c.width, c.height);
      for (const p of parts) {
        p.y += p.vy; p.x += p.vx + Math.sin(p.y / 40); p.rot += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.col; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
      if (++frames < 240 && parts.some((p) => p.y < c.height + 30)) requestAnimationFrame(anim);
      else c.remove();
    })();
  }

  // ---------- старт ----------
  const startParam = (TG && TG.initDataUnsafe && TG.initDataUnsafe.start_param) || new URLSearchParams(location.search).get("tgWebAppStartParam") || "";
  if (!location.hash && /^g_[A-Za-z0-9_-]+$/.test(startParam)) location.hash = "#/g/" + startParam.slice(2);
  else if (!location.hash && /^card_[A-Za-z0-9_-]+$/.test(startParam)) location.hash = "#/card/" + startParam.slice(5);
  loadBookmarks().finally(route);
})();
