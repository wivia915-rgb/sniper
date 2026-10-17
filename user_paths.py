"""Per-user data directories (config, dedup, browser profile)."""

from __future__ import annotations

from pathlib import Path

from user_auth import User

APP_ROOT = Path(__file__).resolve().parent
USERS_ROOT = APP_ROOT / "data" / "users"
LEGACY_CONFIG = APP_ROOT / "sniper_config.json"


def user_root(user: User) -> Path:
    return USERS_ROOT / str(user.id)


def user_config_path(user: User) -> Path:
    return user_root(user) / "sniper_config.json"


def user_seen_path(user: User) -> Path:
    return user_root(user) / "seen_listings.json"


def user_browser_profile_dir(user: User) -> Path:
    return user_root(user) / "browser_profile"


def ensure_user_workspace(user: User) -> None:
    root = user_root(user)
    root.mkdir(parents=True, exist_ok=True)
    user_browser_profile_dir(user).mkdir(parents=True, exist_ok=True)
    cfg = user_config_path(user)
    if not cfg.is_file() and LEGACY_CONFIG.is_file():
        cfg.write_text(LEGACY_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    elif not cfg.is_file():
        from sniper_config import DEFAULTS, save_config

        save_config(dict(DEFAULTS), path=cfg)
