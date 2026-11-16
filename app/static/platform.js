/* Мост к платформам: Telegram / VK / Одноклассники.
 *
 * Одно Mini App в трёх мессенджерах. VK Bridge официальный (MIT) и бесплатный,
 * приложение регистрируется один раз в VK Mini Apps и автоматически доступно
 * в витрине ОК — отдельная разработка под ОК не нужна.
 *
 * Что даёт каждая платформа:
 *   Telegram — initData (HMAC на бэкенде), тема, кнопка «назад», шеринг ссылки;
 *   VK/ОК    — подписанные launch params, VKWebAppShare, VKWebAppShowStoryBox
 *             (как раз для «вашего кино»: ролик уходит в историю одним тапом).
 *
 * Правило: без подписи ничего не считаем авторизованным — id из URL не доверяем.
 */
(function () {
  "use strict";

  const bridge = {
    kind: "web",
    ready: false,
    params: {},
    user: null,
  };

  function qs() {
    return new URLSearchParams(location.search);
  }

  function tgWebApp() {
    const tg = window.Telegram && window.Telegram.WebApp;
    return tg && tg.initData ? tg : null;
  }

  function vkBridge() {
    // vk-bridge подключается тегом <script> (см. index.html) и кладёт window.vkBridge
    return window.vkBridge || null;
  }

  function detect() {
    if (tgWebApp()) return "telegram";
    const search = qs();
    if (search.get("vk_app_id") && search.get("sign")) return "vk";
    if (search.get("ok_app_id")) return "ok";
    if (vkBridge()) return "vk";
    return "web";
  }

  async function init() {
    bridge.kind = detect();
    bridge.params = Object.fromEntries(qs());

    if (bridge.kind === "telegram") {
      const tg = tgWebApp();
      try { tg.ready(); tg.expand(); } catch (e) { /* старые клиенты */ }
      bridge.ready = true;
      return bridge;
    }

    const vk = vkBridge();
    if (vk && (bridge.kind === "vk" || bridge.kind === "ok")) {
      try {
        await vk.send("VKWebAppInit");
        bridge.ready = true;
      } catch (e) {
        bridge.ready = false;
      }
    }
    return bridge;
  }

  /** Заголовки для запросов к бэкенду: каждая платформа доказывает личность по-своему. */
  function authHeaders() {
    if (bridge.kind === "telegram") {
      const tg = tgWebApp();
      return { "X-Telegram-Init-Data": tg ? tg.initData : "" };
    }
    if (bridge.kind === "vk" || bridge.kind === "ok") {
      return { "X-VK-Launch-Params": location.search.replace(/^\?/, "") };
    }
    return {};
  }

  /** Авторизоваться на бэкенде. Возвращает пользователя или null. */
  async function login() {
    const headers = authHeaders();
    const path = bridge.kind === "telegram" ? "/api/auth/telegram" : "/api/auth/vk";
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, headers),
        body: JSON.stringify({ init_data: headers["X-Telegram-Init-Data"] || "",
                               launch_params: headers["X-VK-Launch-Params"] || "" }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      bridge.user = data.user || null;
      return bridge.user;
    } catch (e) {
      return null;
    }
  }

  /** Поделиться ссылкой на подарок (в VK/ОК — нативный диалог). */
  async function share(url, text) {
    const vk = vkBridge();
    if (vk && (bridge.kind === "vk" || bridge.kind === "ok")) {
      try {
        await vk.send("VKWebAppShare", { link: url });
        return true;
      } catch (e) { /* падаем в веб-шаринг */ }
    }
    const tg = tgWebApp();
    if (tg) {
      tg.switchInlineQuery ? tg.switchInlineQuery(url) : null;
      const shareUrl = "https://t.me/share/url?url=" + encodeURIComponent(url) +
        (text ? "&text=" + encodeURIComponent(text) : "");
      window.open(shareUrl, "_blank", "noopener");
      return true;
    }
    if (navigator.share) {
      try { await navigator.share({ url: url, text: text || "" }); return true; } catch (e) { return false; }
    }
    return false;
  }

  /** «Ваше кино» в историю: в VK/ОК история открывается нативно, без своего редактора. */
  async function shareStory(videoUrl, link) {
    const vk = vkBridge();
    if (!vk || (bridge.kind !== "vk" && bridge.kind !== "ok")) return false;
    try {
      await vk.send("VKWebAppShowStoryBox", {
        background_type: "image",
        url: videoUrl,
        attachment: { text: "open", type: "url", url: link },
      });
      return true;
    } catch (e) {
      return false;
    }
  }

  window.scenaPlatform = { bridge, init, login, authHeaders, share, shareStory, detect };
})();
