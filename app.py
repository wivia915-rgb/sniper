"""
Streamlit UI for Facebook Marketplace vehicle / motorcycle sniper.

Run:  streamlit run app.py

Time filtering for alerts uses vehicle_dom.parse_vehicle_card_age_minutes():
empty / Just listed → 0 minutes; strict minute strings only (no ID substring parsing).
"""

from __future__ import annotations

import importlib

import streamlit as st

import sniper_engine as sniper_engine_module
from seen_listings import clear_seen, seen_count
from openai_listing_analyzer import OpenAIListingAnalyzer
from sniper_config import (
    CITY_OPTIONS,
    DEFAULT_SEARCH_KEYWORDS,
    TURBO_SEARCH_KEYWORDS,
    VEHICLE_KEYWORDS,
    load_config,
    parse_search_keywords,
    save_config,
    SniperSettings,
)
from user_auth import User, authenticate, init_db, register_user
from user_paths import (
    ensure_user_workspace,
    user_browser_profile_dir,
    user_config_path,
    user_seen_path,
)

SNIPER_ENGINE_VERSION = "1.5.0"


def _current_user() -> User:
    user = st.session_state.get("auth_user")
    if user is None:
        st.error("Session expired — log in again.")
        st.stop()
    return user


def _make_engine_for_user(user: User) -> sniper_engine_module.SniperEngine:
    ensure_user_workspace(user)
    return sniper_engine_module.SniperEngine(
        profile_dir=user_browser_profile_dir(user),
        seen_path=user_seen_path(user),
    )


def _get_engine() -> sniper_engine_module.SniperEngine:
    """Per-session engine with isolated browser profile + seen store."""
    user = _current_user()
    uid = user.id
    if (
        "sniper_engine" not in st.session_state
        or st.session_state.get("engine_user_id") != uid
    ):
        prev = st.session_state.get("sniper_engine")
        if prev is not None and prev.is_running():
            prev.stop()
        st.session_state.sniper_engine = _make_engine_for_user(user)
        st.session_state.engine_user_id = uid
    return st.session_state.sniper_engine


def _reload_engine_module() -> None:
    importlib.reload(sniper_engine_module)
    st.session_state.sniper_engine = _make_engine_for_user(_current_user())


def _do_save_settings(
    fb_email: str,
    fb_password: str,
    tg_token: str,
    tg_chat: str,
    oai_enabled: bool,
    oai_key: str,
    oai_model: str,
    oai_min_score: int,
    oai_strict: bool,
    oai_after: bool,
    turbo_mode: bool,
    city: str,
    keywords_text: str,
    time_filter_enabled: bool,
    max_age: int,
    check_interval: int,
    top_limit: int,
    headless: bool,
    config_path,
) -> None:
    st.session_state.pop("keywords_override", None)
    st.session_state.pop("turbo_preset_interval", None)
    st.session_state.pop("turbo_preset_top", None)
    settings: SniperSettings = {
        "facebook_email": fb_email.strip(),
        "facebook_password": fb_password,
        "telegram_bot_token": tg_token.strip(),
        "telegram_chat_id": tg_chat.strip(),
        "openai_enabled": oai_enabled,
        "openai_api_key": oai_key.strip(),
        "openai_model": (oai_model or "gpt-4o-mini").strip(),
        "openai_min_snipe_score": int(oai_min_score),
        "openai_strict_motorcycle": oai_strict,
        "openai_after_alert": oai_after,
        "turbo_mode": turbo_mode,
        "city": city,
        "search_keywords": parse_search_keywords(keywords_text),
        "time_filter_enabled": time_filter_enabled,
        "max_alert_age_minutes": int(max_age),
        "check_interval_seconds": int(check_interval),
        "top_listings_limit": int(top_limit),
        "headless": headless,
    }
    save_config(settings, path=config_path)
    st.success("Settings saved.")
    st.rerun()


def _render_auth_screen() -> None:
    st.title("🚗 Marketplace Sniper")
    st.caption("تسجيل حساب / دخول — كل مستخدم عندو إعداداتو و Telegram و Facebook منفصلين.")
    login_tab, register_tab = st.tabs(["تسجيل الدخول", "إنشاء حساب"])
    with login_tab:
        with st.form("login_form"):
            lu = st.text_input("Username", autocomplete="username")
            lp = st.text_input("Password", type="password", autocomplete="current-password")
            if st.form_submit_button("دخول", type="primary"):
                user = authenticate(lu, lp)
                if user:
                    ensure_user_workspace(user)
                    st.session_state.auth_user = user
                    st.session_state.pop("sniper_engine", None)
                    st.rerun()
                else:
                    st.error("Username أو password غلط.")
    with register_tab:
        with st.form("register_form"):
            ru = st.text_input("Username جديد", autocomplete="username")
            rp = st.text_input("Password (8+)", type="password", autocomplete="new-password")
            rp2 = st.text_input("Confirm password", type="password", autocomplete="new-password")
            if st.form_submit_button("تسجيل", type="primary"):
                if rp != rp2:
                    st.error("Passwords ما متطابقينش.")
                else:
                    ok, msg = register_user(ru, rp)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)


st.set_page_config(
    page_title="Marketplace Vehicle Sniper",
    page_icon="🚗",
    layout="wide",
)

init_db()
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if st.session_state.auth_user is None:
    _render_auth_screen()
    st.stop()

auth_user: User = st.session_state.auth_user
ensure_user_workspace(auth_user)
_config_path = user_config_path(auth_user)
_seen_path = user_seen_path(auth_user)

with st.sidebar:
    st.markdown(f"**👤 {auth_user.username}**")
    st.caption("حسابك — config و seen listings منفصلين.")
    if st.button("تسجيل الخروج", use_container_width=True):
        _eng = st.session_state.get("sniper_engine")
        if _eng is not None and _eng.is_running():
            _eng.stop()
        st.session_state.auth_user = None
        st.session_state.pop("sniper_engine", None)
        st.session_state.pop("engine_user_id", None)
        st.rerun()

engine = _get_engine()
saved = load_config(_config_path)

st.title("🚗 Facebook Marketplace Vehicle Sniper")
st.caption(
    "Sniper **moto / scooter فقط** — إعدادات محفوظة لحساب "
    f"**{auth_user.username}** (local server)."
)

col_form, col_status = st.columns([1, 1], gap="large")

with col_form:
    st.subheader("Settings")
    with st.form("sniper_settings_form"):
        fb_email = st.text_input(
            "Facebook Email",
            value=saved["facebook_email"],
            autocomplete="off",
        )
        fb_password = st.text_input(
            "Facebook Password",
            value=saved["facebook_password"],
            type="password",
            autocomplete="off",
        )
        st.divider()
        tg_token = st.text_input(
            "TELEGRAM_BOT_TOKEN",
            value=saved["telegram_bot_token"],
            type="password",
            autocomplete="off",
        )
        tg_chat = st.text_input(
            "TELEGRAM_CHAT_ID",
            value=saved["telegram_chat_id"],
            autocomplete="off",
        )
        st.divider()
        oai_enabled = st.checkbox(
            "OpenAI — تحليل + snipe score",
            value=bool(saved.get("openai_enabled", False)),
            help="كيخدم من بعد فلتر الوقت — يزيد 🎯 score و 🤖 ملخص فـ Telegram.",
        )
        oai_key = st.text_input(
            "OPENAI_API_KEY",
            value=saved.get("openai_api_key", ""),
            type="password",
            disabled=not oai_enabled,
            autocomplete="off",
        )
        oai_model = st.text_input(
            "OpenAI model",
            value=saved.get("openai_model", "gpt-4o-mini"),
            disabled=not oai_enabled,
        )
        oai_min_score = st.slider(
            "Min snipe score (1–10)",
            min_value=1,
            max_value=10,
            value=int(saved.get("openai_min_snipe_score", 5)),
            disabled=not oai_enabled,
            help="غير الإعلانات ≥ هاد النقطة كيوصلو Telegram.",
        )
        oai_strict = st.checkbox(
            "OpenAI: رفض إلا moto/scooter",
            value=bool(saved.get("openai_strict_motorcycle", True)),
            disabled=not oai_enabled,
        )
        oai_after = st.checkbox(
            "OpenAI بعد Telegram (أسرع)",
            value=bool(saved.get("openai_after_alert", False)),
            disabled=not oai_enabled,
            help="Telegram فوري + رسالة 🤖 ثانية — الفلتر DOM/time كيبقى دقيق.",
        )
        st.divider()
        turbo_mode = st.checkbox(
            "⚡ Turbo — scan أسرع",
            value=bool(saved.get("turbo_mode", False)),
            help="22 feeds/cycle، صور FB مقطوعة، أولوية للإعلانات fresh فالكارت.",
        )
        city = st.selectbox(
            "City",
            options=list(CITY_OPTIONS),
            index=list(CITY_OPTIONS).index(saved["city"])
            if saved["city"] in CITY_OPTIONS
            else 0,
        )
        kw_default = st.session_state.get("keywords_override") or saved.get(
            "search_keywords"
        ) or list(DEFAULT_SEARCH_KEYWORDS)
        keywords_text = st.text_area(
            "الكلمات المفتاحية / Search keywords",
            value=", ".join(kw_default),
            height=120,
            help=(
                f"كل كلمة = بحث vehicles منفصل. القائمة الكاملة: "
                f"{len(VEHICLE_KEYWORDS)} كلمة — زر تحت الفورم."
            ),
        )
        time_filter_enabled = st.checkbox(
            "Just listed / عمر ≤ دقائق (فلتر الوقت)",
            value=bool(saved.get("time_filter_enabled", True)),
            help="مفعّل: غير Just listed أو إعلان ≤ MAX_ALERT_AGE_MINUTES.",
        )
        max_age = st.number_input(
            "MAX_ALERT_AGE_MINUTES",
            min_value=1,
            max_value=120,
            value=int(saved.get("max_alert_age_minutes", 7)),
            step=1,
            disabled=not time_filter_enabled,
            help="افتراضي **7** — ساعات/أيام ما كيوصلوش Telegram.",
        )
        _interval_default = int(
            st.session_state.get("turbo_preset_interval", saved["check_interval_seconds"])
        )
        check_interval = st.number_input(
            "Check interval (seconds)",
            min_value=10,
            max_value=120,
            value=_interval_default,
            step=1,
            help="Turbo: 10–12s. أقل من 10 = block Facebook.",
        )
        _top_default = int(
            st.session_state.get("turbo_preset_top", saved["top_listings_limit"])
        )
        top_limit = st.number_input(
            "Top listings per scan",
            min_value=5,
            max_value=50,
            value=_top_default,
            step=1,
            help="Turbo: 18–20. أكثر = catch أسرع.",
        )
        headless = st.checkbox(
            "Headless browser",
            value=bool(saved["headless"]),
            help="Turn OFF for first login / 2FA / checkpoint — a Chromium window will open.",
        )
        btn_save, btn_oai_test = st.columns(2)
        with btn_save:
            save_clicked = st.form_submit_button("💾 Save Settings", type="primary")
        with btn_oai_test:
            test_oai_form = st.form_submit_button(
                "🤖 Test OpenAI",
                disabled=not oai_enabled,
            )

    kw_col1, kw_col2 = st.columns(2)
    with kw_col1:
        if st.button(f"↺ Mots-clés moto ({len(VEHICLE_KEYWORDS)})"):
            st.session_state["keywords_override"] = list(VEHICLE_KEYWORDS)
            st.rerun()
    with kw_col2:
        if st.button(f"⚡ Turbo preset ({len(TURBO_SEARCH_KEYWORDS)} kw)"):
            st.session_state["keywords_override"] = list(TURBO_SEARCH_KEYWORDS)
            st.session_state["turbo_preset_interval"] = 10
            st.session_state["turbo_preset_top"] = 18
            st.session_state["turbo_preset_flag"] = True
            st.rerun()
    if st.session_state.pop("turbo_preset_flag", False):
        st.info(
            "Turbo preset: 12 كلمة + interval 10 + top 18 — فعّل **⚡ Turbo** و **Save**."
        )

    if save_clicked:
        if oai_enabled and not oai_key.strip():
            st.error("OpenAI مفعّل — عبّي **OPENAI_API_KEY** و Save.")
        else:
            _do_save_settings(
                fb_email,
                fb_password,
                tg_token,
                tg_chat,
                oai_enabled,
                oai_key,
                oai_model,
                oai_min_score,
                oai_strict,
                oai_after,
                turbo_mode,
                city,
                keywords_text,
                time_filter_enabled,
                max_age,
                check_interval,
                top_limit,
                headless,
                _config_path,
            )

    if test_oai_form:
        if not oai_enabled:
            st.error("فعّل OpenAI أولاً.")
        elif not oai_key.strip():
            st.error("OPENAI_API_KEY فارغ.")
        else:
            oai = OpenAIListingAnalyzer(oai_key.strip(), model=oai_model.strip())
            ok, msg = oai.send_test()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

with col_status:
    st.subheader("Control")
    cfg = load_config(_config_path)
    missing = []
    if not cfg["facebook_email"] or not cfg["facebook_password"]:
        missing.append("Facebook credentials")
    if not cfg["telegram_bot_token"] or not cfg["telegram_chat_id"]:
        missing.append("Telegram settings")
    if missing:
        st.warning("Save settings first: " + ", ".join(missing))

    if cfg.get("openai_enabled") and cfg.get("openai_api_key"):
        st.caption(
            f"OpenAI: **ON** — min score **{cfg.get('openai_min_snipe_score', 5)}/10** "
            f"({cfg.get('openai_model', 'gpt-4o-mini')})"
        )
    elif cfg.get("openai_enabled"):
        st.warning("OpenAI ON ولكن **API key** ناقص — Save Settings.")

    if cfg.get("turbo_mode"):
        st.caption("⚡ **Turbo ON** — fast scan (v1.4)")

    status_color = {
        "Running": "🟢",
        "Paused": "⚪",
        "Stopping...": "🟡",
    }.get(engine.status, "⚪")

    st.metric("Status", f"{status_color} {engine.status}")
    st.metric("Alerts sent (session)", engine.alerts_sent)
    if engine.last_scan_at:
        st.caption(f"Last scan: {engine.last_scan_at}")
    if engine.last_scan_summary:
        st.info(engine.last_scan_summary)
    st.caption(
        "Telegram = **إعلان جديد** (ID جديد). نفس الإعلانات فالأعلى → « déjà vus » — ماشي عطل."
    )
    if engine.last_error:
        st.error(engine.last_error)
        if 'button[name="login"]' in engine.last_error:
            st.info(
                "This error is from an **older** login handler. "
                "Click **Start Sniper** again (reloads engine v"
                f"{SNIPER_ENGINE_VERSION}) or restart: `Ctrl+C` then `streamlit run app.py`."
            )

    btn_start, btn_stop = st.columns(2)
    with btn_start:
        if st.button("▶ Start Sniper", type="primary", disabled=engine.is_running()):
            _reload_engine_module()
            engine = _get_engine()
            if engine.start(cfg):
                st.toast("Sniper started")
                st.rerun()
    with btn_stop:
        if st.button("⏹ Stop Sniper", disabled=not engine.is_running()):
            engine.stop()
            st.toast("Stopping sniper...")
            st.rerun()

    n_seen = seen_count(_seen_path)
    st.caption(
        f"**{n_seen}** listing ID(s) (dedup) — Telegram sends **only new** IDs."
    )
    if st.button(
        "🗑 Clear seen listings",
        help="Reset dedup so the next scan can alert again (testing / after long pause).",
        disabled=engine.is_running(),
    ):
        clear_seen(_seen_path)
        st.success("Cleared seen listings for your account.")
        st.rerun()

st.subheader("Live log")
if engine.is_running():
    st.caption("Auto-refreshing while the sniper runs…")

log_box = st.empty()
log_lines = list(engine.logs)
if log_lines:
    log_box.code("\n".join(reversed(log_lines[-80:])), language=None)
else:
    log_box.info("No log entries yet. Save settings and press Start Sniper.")

if engine.is_running():
    import time

    time.sleep(2)
    st.rerun()
