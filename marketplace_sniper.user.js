// ==UserScript==
// @name         Facebook Marketplace Sniper (Telegram)
// @namespace    marketplace-sniper
// @version      1.1.0
// @description  Real-time Marketplace feed sniper with Telegram alerts, settings UI, localStorage dedup
// @author       You
// @match        https://www.facebook.com/marketplace/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      api.telegram.org
// ==/UserScript==

(function () {
  "use strict";

  const STORAGE_KEY = "fb_marketplace_sniper_seen_v1";
  const MAX_SEEN_STORE = 5000;
  const UI_ID = "fb-marketplace-sniper-settings";

  /** @returns {Record<string, string|number|boolean>} */
  function loadConfig() {
    return {
      TELEGRAM_BOT_TOKEN: GM_getValue("TELEGRAM_BOT_TOKEN", ""),
      TELEGRAM_CHAT_ID: GM_getValue("TELEGRAM_CHAT_ID", ""),
      MARKETPLACE_CITY: GM_getValue("MARKETPLACE_CITY", ""),
      SEARCH_KEYWORD: GM_getValue("SEARCH_KEYWORD", ""),
      CHECK_INTERVAL_MS: GM_getValue("CHECK_INTERVAL_MS", 12_000),
      TOP_LISTINGS_LIMIT: GM_getValue("TOP_LISTINGS_LIMIT", 5),
      MAX_ALERT_AGE_MINUTES: GM_getValue("MAX_ALERT_AGE_MINUTES", 10),
      TIME_FILTER_ENABLED: GM_getValue("TIME_FILTER_ENABLED", true),
      SNIPER_ALLOW_EMPTY_TIME: GM_getValue("SNIPER_ALLOW_EMPTY_TIME", true),
      DEBUG: GM_getValue("DEBUG", false),
    };
  }

  let CONFIG = loadConfig();
  let scanRunning = false;
  let sniperStarted = false;
  let scanTimer = null;
  let domObserver = null;

  function refreshConfig() {
    CONFIG = loadConfig();
  }

  function log(...args) {
    if (CONFIG.DEBUG) console.log("[MarketplaceSniper]", ...args);
  }

  function warn(...args) {
    console.warn("[MarketplaceSniper]", ...args);
  }

  function cityToSlug(city) {
    return String(city || "")
      .trim()
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, "")
      .replace(/[^a-z0-9-]/g, "");
  }

  function buildMarketplaceSearchUrl(city, keyword) {
    const slug = cityToSlug(city) || "category/search";
    const params = new URLSearchParams();
    params.set("sortBy", "creation_time_descend");
    const q = String(keyword || "").trim();
    if (q) params.set("query", q);
    return `https://www.facebook.com/marketplace/${slug}/search/?${params.toString()}`;
  }

  function isConfigured() {
    refreshConfig();
    return (
      Boolean(CONFIG.TELEGRAM_BOT_TOKEN) &&
      Boolean(CONFIG.TELEGRAM_CHAT_ID) &&
      Boolean(CONFIG.MARKETPLACE_CITY)
    );
  }

  function loadSeenSet() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr : []);
    } catch {
      return new Set();
    }
  }

  function saveSeenSet(seen) {
    const arr = Array.from(seen).slice(-MAX_SEEN_STORE);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(arr));
  }

  function normalizeListingUrl(href) {
    if (!href) return "";
    const path = href.split("?")[0];
    if (path.startsWith("http")) return path;
    return `https://www.facebook.com${path}`;
  }

  function extractListingId(href) {
    const m = (href || "").match(/\/marketplace\/item\/(\d+)/);
    return m ? m[1] : null;
  }

  function isPriceText(t) {
    return /^(\$|€|£|FREE|MAD\s*\d|MAD\d|\d{1,3}(,\d{3})+|Free)$/i.test(
      (t || "").trim()
    );
  }

  function isPromotionalTime(t) {
    return /^(just\s+listed|newly\s+listed)$/i.test((t || "").trim());
  }

  function isTimeOnlyText(t) {
    const text = (t || "").trim();
    if (!text || text.length > 50) return false;
    if (/MAD|DH|\$|€|£|\d{1,3},\d{3}|Morocco|Maroc|\n/i.test(text)) return false;
    const patterns = [
      /^just listed$/i,
      /^just now$/i,
      /^newly listed$/i,
      /^\d+\s*mins?\s+ago$/i,
      /^\d+\s*minutes?\s+ago$/i,
      /^\d+\s*m(in|ins|inute|inutes)?\s*(ago)?$/i,
      /^(?:à|a)\s*l\s*'?instant$/i,
      /^maintenant$/i,
      /^il y a \d+\s*min(?:ute)?s?$/i,
      /^منذ\s+\d+\s*د/i,
      /^الآن$/i,
      /^الان$/i,
    ];
    return patterns.some((re) => re.test(text)) || isPromotionalTime(text);
  }

  function collectLeafTexts(root) {
    const out = [];
    if (!root) return out;
    root.querySelectorAll('span[dir="auto"], span, div[dir="auto"]').forEach((el) => {
      if (el.children.length > 0) return;
      const t = (el.textContent || "").trim();
      if (t) out.push(t);
    });
    return out;
  }

  function findTimePosted(roots, sniperMode) {
    for (const root of roots) {
      const leaves = collectLeafTexts(root);
      for (const t of leaves) {
        if (!sniperMode && isPromotionalTime(t)) continue;
        if (isTimeOnlyText(t) || (sniperMode && isPromotionalTime(t))) return t;
      }
    }
    return "";
  }

  function parseAgeMinutes(timePosted) {
    const t = (timePosted || "").trim().toLowerCase();
    if (!t) return 0;
    if (/^\d{5,}$/.test(t)) return 0;
    if (/^(just listed|just now|newly listed|(?:à|a)\s*l\s*'?instant|maintenant|الآن|الان)$/i.test(t)) {
      return 0;
    }
    if (/\b(days?|weeks?|months?|years?|jours?|semaines?)\b/i.test(t)) {
      return 9999;
    }
    if (/\b(hour|hours|heure|heures|hr|hrs)\b/i.test(t) || /il y a \d+ heure/i.test(t)) {
      return 9999;
    }
    let m = t.match(/^(\d{1,3})\s*mins?\s+ago$/);
    if (m) return parseInt(m[1], 10);
    m = t.match(/^(\d{1,3})\s*minutes?\s+ago$/);
    if (m) return parseInt(m[1], 10);
    m = t.match(/^il y a (\d{1,3})\s*min(?:ute)?s?$/);
    if (m) return parseInt(m[1], 10);
    m = t.match(/^(\d{1,3})m$/);
    if (m) return parseInt(m[1], 10);
    return 0;
  }

  function isListingFresh(timePosted) {
    refreshConfig();
    if (!CONFIG.TIME_FILTER_ENABLED) return true;
    const raw = (timePosted || "").trim();
    if (CONFIG.SNIPER_ALLOW_EMPTY_TIME && !raw) {
      log("empty time -> allowed (sniper feed order)");
      return true;
    }
    const age = parseAgeMinutes(raw);
    return age <= Number(CONFIG.MAX_ALERT_AGE_MINUTES);
  }

  function parseListingsFromDom(limit) {
    const anchors = Array.from(
      document.querySelectorAll('a[href*="/marketplace/item/"]')
    );
    const seen = new Set();
    const results = [];

    for (const a of anchors) {
      const href = a.getAttribute("href") || "";
      const id = extractListingId(href);
      if (!id || seen.has(id)) continue;
      seen.add(id);

      let card = a;
      for (let i = 0; i < 6 && card.parentElement; i++) {
        card = card.parentElement;
      }

      const img = a.querySelector("img") || card.querySelector("img");
      const imageUrl = img ? img.src || img.getAttribute("src") || "" : "";

      const searchRoots = [a, card];
      const leaves = [];
      for (const root of searchRoots) {
        for (const t of collectLeafTexts(root)) {
          if (!leaves.includes(t)) leaves.push(t);
        }
      }

      const timePosted = findTimePosted(searchRoots, true);
      const price = leaves.find((t) => isPriceText(t)) || "";
      const location =
        leaves.find(
          (t) => t.length < 100 && /,/.test(t) && !isPriceText(t) && !isTimeOnlyText(t)
        ) || "";

      let title = (a.getAttribute("aria-label") || "").trim();
      if (!title) {
        title =
          leaves.find(
            (t) =>
              t.length > 3 &&
              !isPriceText(t) &&
              !isTimeOnlyText(t) &&
              t !== location &&
              t !== timePosted
          ) || "Untitled listing";
      }

      results.push({
        listing_id: id,
        title,
        price: price || "—",
        location: location || "—",
        url: normalizeListingUrl(href),
        image_url: imageUrl,
        time_posted: timePosted || "",
      });

      if (results.length >= limit) break;
    }

    return results;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function formatTelegramCaption(item) {
    const lines = [
      `<b>${escapeHtml(item.title)}</b>`,
      `💰 <b>${escapeHtml(item.price)}</b>`,
      `📍 ${escapeHtml(item.location)}`,
    ];
    if (item.time_posted) {
      lines.push(`🕐 ${escapeHtml(item.time_posted)}`);
    }
    lines.push(`\n<a href="${escapeHtml(item.url)}">Open listing</a>`);
    return lines.join("\n");
  }

  function telegramRequest(method, payload) {
    return new Promise((resolve, reject) => {
      refreshConfig();
      const token = CONFIG.TELEGRAM_BOT_TOKEN;
      if (!token || !CONFIG.TELEGRAM_CHAT_ID) {
        reject(new Error("Telegram not configured"));
        return;
      }
      GM_xmlhttpRequest({
        method: "POST",
        url: `https://api.telegram.org/bot${token}/${method}`,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(payload),
        onload(res) {
          if (res.status >= 200 && res.status < 300) {
            resolve(res.responseText);
          } else {
            reject(new Error(`Telegram HTTP ${res.status}: ${res.responseText}`));
          }
        },
        onerror: () => reject(new Error("Telegram network error")),
      });
    });
  }

  async function sendTelegramAlert(item) {
    refreshConfig();
    const caption = formatTelegramCaption(item);
    const base = {
      chat_id: CONFIG.TELEGRAM_CHAT_ID,
      parse_mode: "HTML",
      disable_web_page_preview: false,
    };

    if (item.image_url) {
      try {
        await telegramRequest("sendPhoto", {
          ...base,
          photo: item.image_url,
          caption: caption.slice(0, 1024),
        });
        return;
      } catch (e) {
        warn("sendPhoto failed, falling back to sendMessage", e);
      }
    }

    await telegramRequest("sendMessage", {
      ...base,
      text: caption.slice(0, 4096),
    });
  }

  async function scanAndAlert() {
    if (scanRunning) return;
    if (!isConfigured()) return;

    scanRunning = true;

    try {
      refreshConfig();
      const limit = Number(CONFIG.TOP_LISTINGS_LIMIT) || 5;
      const listings = parseListingsFromDom(limit);
      log(`Scanned ${listings.length} top listing(s)`);

      const seen = loadSeenSet();
      let sent = 0;

      for (const item of listings) {
        if (seen.has(item.listing_id)) {
          log("skip seen", item.listing_id);
          continue;
        }
        if (!isListingFresh(item.time_posted)) {
          log("skip time filter", item.listing_id, item.time_posted);
          continue;
        }

        try {
          await sendTelegramAlert(item);
          seen.add(item.listing_id);
          saveSeenSet(seen);
          sent += 1;
          console.info("[MarketplaceSniper] Alert sent:", item.title, item.url);
        } catch (err) {
          warn("Telegram alert failed for", item.listing_id, err);
        }
      }

      if (sent > 0) {
        log(`Sent ${sent} new alert(s)`);
      }
    } finally {
      scanRunning = false;
    }
  }

  function stopSniperLoop() {
    if (scanTimer) {
      clearInterval(scanTimer);
      scanTimer = null;
    }
    if (domObserver) {
      domObserver.disconnect();
      domObserver = null;
    }
    sniperStarted = false;
  }

  function startSniperLoop() {
    if (sniperStarted) return;
    if (!isConfigured()) return;

    refreshConfig();
    const interval = Math.max(
      10_000,
      Math.min(15_000, Number(CONFIG.CHECK_INTERVAL_MS) || 12_000)
    );

    console.info(
      `[MarketplaceSniper] Active — every ${interval / 1000}s, city=${CONFIG.MARKETPLACE_CITY}`
    );

    scanAndAlert();
    scanTimer = setInterval(scanAndAlert, interval);

    domObserver = new MutationObserver(() => {
      scanAndAlert();
    });
    domObserver.observe(document.body, { childList: true, subtree: true });
    sniperStarted = true;
  }

  function injectStyles() {
    if (document.getElementById("fb-sniper-styles")) return;
    const style = document.createElement("style");
    style.id = "fb-sniper-styles";
    style.textContent = `
      #${UI_ID} {
        position: fixed; z-index: 2147483646; top: 72px; right: 16px;
        width: min(380px, calc(100vw - 32px));
        font-family: system-ui, Segoe UI, Roboto, sans-serif;
        background: #fff; color: #111; border-radius: 12px;
        box-shadow: 0 12px 40px rgba(0,0,0,.25);
        border: 1px solid #e5e7eb; overflow: hidden;
      }
      #${UI_ID}.hidden { display: none; }
      #${UI_ID} header {
        padding: 14px 16px; background: linear-gradient(135deg,#1877f2,#0d65d9);
        color: #fff; font-weight: 700; font-size: 15px;
      }
      #${UI_ID} .body { padding: 14px 16px 16px; }
      #${UI_ID} label {
        display: block; font-size: 12px; font-weight: 600; margin: 10px 0 4px; color: #374151;
      }
      #${UI_ID} input {
        width: 100%; box-sizing: border-box; padding: 9px 10px;
        border: 1px solid #d1d5db; border-radius: 8px; font-size: 13px;
      }
      #${UI_ID} input:focus { outline: 2px solid #1877f2; border-color: #1877f2; }
      #${UI_ID} .hint { font-size: 11px; color: #6b7280; margin-top: 4px; line-height: 1.35; }
      #${UI_ID} .actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
      #${UI_ID} button {
        flex: 1; min-width: 120px; padding: 10px 12px; border: 0; border-radius: 8px;
        font-weight: 600; cursor: pointer; font-size: 13px;
      }
      #${UI_ID} .btn-primary { background: #1877f2; color: #fff; }
      #${UI_ID} .btn-secondary { background: #f3f4f6; color: #111; }
      #${UI_ID} .status {
        margin-top: 10px; font-size: 12px; padding: 8px 10px; border-radius: 8px;
        background: #f0fdf4; color: #166534; display: none;
      }
      #${UI_ID} .status.error { background: #fef2f2; color: #991b1b; display: block; }
      #${UI_ID} .status.ok { display: block; }
      #fb-sniper-fab {
        position: fixed; z-index: 2147483645; bottom: 20px; right: 20px;
        width: 52px; height: 52px; border-radius: 50%; border: 0;
        background: #1877f2; color: #fff; font-size: 22px; cursor: pointer;
        box-shadow: 0 6px 20px rgba(0,0,0,.3);
      }
    `;
    document.documentElement.appendChild(style);
  }

  function createSettingsPanel() {
    injectStyles();
    if (document.getElementById(UI_ID)) return;

    refreshConfig();

    const panel = document.createElement("div");
    panel.id = UI_ID;
    panel.innerHTML = `
      <header>🎯 Marketplace Sniper — الإعدادات</header>
      <div class="body">
        <label for="sniper-token">TELEGRAM_BOT_TOKEN</label>
        <input id="sniper-token" type="password" placeholder="123456789:ABC..." autocomplete="off" />
        <p class="hint">من @BotFather — يتخزّن محلياً فقط (Tampermonkey).</p>

        <label for="sniper-chat">TELEGRAM_CHAT_ID</label>
        <input id="sniper-chat" type="text" placeholder="123456789" autocomplete="off" />
        <p class="hint">معرّف المحادثة / القناة ديالك.</p>

        <label for="sniper-city">المدينة / Ville (Marketplace)</label>
        <input id="sniper-city" type="text" placeholder="casablanca, rabat, marrakech..." />
        <p class="hint">Slug ديال المدينة فـ Facebook (بلا مسافات). مثال: <b>casablanca</b></p>

        <label for="sniper-keyword">كلمة البحث (اختياري)</label>
        <input id="sniper-keyword" type="text" placeholder="iphone, voiture..." />
        <p class="hint">Khaliha فارغة = كل الإعلانات الجديدة فالمدينة.</p>

        <div class="actions">
          <button type="button" class="btn-primary" id="sniper-save">💾 Enregistrer / حفظ</button>
          <button type="button" class="btn-secondary" id="sniper-open">🔍 Ouvrir recherche</button>
        </div>
        <div class="status" id="sniper-status"></div>
      </div>
    `;

    const fab = document.createElement("button");
    fab.id = "fb-sniper-fab";
    fab.type = "button";
    fab.title = "Paramètres Sniper";
    fab.textContent = "⚙️";

    document.body.appendChild(panel);
    document.body.appendChild(fab);

    const tokenEl = panel.querySelector("#sniper-token");
    const chatEl = panel.querySelector("#sniper-chat");
    const cityEl = panel.querySelector("#sniper-city");
    const keywordEl = panel.querySelector("#sniper-keyword");
    const statusEl = panel.querySelector("#sniper-status");

    tokenEl.value = CONFIG.TELEGRAM_BOT_TOKEN || "";
    chatEl.value = CONFIG.TELEGRAM_CHAT_ID || "";
    cityEl.value = CONFIG.MARKETPLACE_CITY || "";
    keywordEl.value = CONFIG.SEARCH_KEYWORD || "";

    function setStatus(msg, isError) {
      statusEl.textContent = msg;
      statusEl.className = "status " + (isError ? "error" : "ok");
    }

    function togglePanel(show) {
      panel.classList.toggle("hidden", !show);
    }

    fab.addEventListener("click", () => {
      refreshConfig();
      tokenEl.value = CONFIG.TELEGRAM_BOT_TOKEN || "";
      chatEl.value = CONFIG.TELEGRAM_CHAT_ID || "";
      cityEl.value = CONFIG.MARKETPLACE_CITY || "";
      keywordEl.value = CONFIG.SEARCH_KEYWORD || "";
      togglePanel(panel.classList.contains("hidden"));
    });

    panel.querySelector("#sniper-save").addEventListener("click", () => {
      const token = tokenEl.value.trim();
      const chat = chatEl.value.trim();
      const city = cityEl.value.trim();
      const keyword = keywordEl.value.trim();

      if (!token || !chat || !city) {
        setStatus("⚠️ Token, Chat ID et Ville sont obligatoires.", true);
        return;
      }

      GM_setValue("TELEGRAM_BOT_TOKEN", token);
      GM_setValue("TELEGRAM_CHAT_ID", chat);
      GM_setValue("MARKETPLACE_CITY", city);
      GM_setValue("SEARCH_KEYWORD", keyword);
      refreshConfig();

      stopSniperLoop();
      startSniperLoop();

      setStatus("✅ Enregistré! Le sniper est actif.", false);
      setTimeout(() => togglePanel(false), 1200);
    });

    panel.querySelector("#sniper-open").addEventListener("click", () => {
      const city = cityEl.value.trim();
      const keyword = keywordEl.value.trim();
      if (!city) {
        setStatus("⚠️ دخل المدينة أولاً.", true);
        return;
      }
      const url = buildMarketplaceSearchUrl(city, keyword);
      window.location.href = url;
    });

    if (!isConfigured()) {
      togglePanel(true);
    } else {
      togglePanel(false);
    }
  }

  function boot() {
    createSettingsPanel();
    if (isConfigured()) {
      startSniperLoop();
    } else {
      warn("Ouvrez ⚙️ pour configurer Telegram et la ville.");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
