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
    if cfg.is_file():
        return
    from sniper_config import DEFAULTS, save_config

    # New accounts always start empty — never copy another user's legacy file.
    if user.id == 1 and LEGACY_CONFIG.is_file():
        cfg.write_text(LEGACY_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        save_config(dict(DEFAULTS), path=cfg)


def reset_user_config(user: User) -> None:
    """Clear this user's sniper settings (passwords, tokens, keys)."""
    from sniper_config import DEFAULTS, save_config

    ensure_user_workspace(user)
    save_config(dict(DEFAULTS), path=user_config_path(user))


__all__ = [
    "ensure_user_workspace",
    "reset_user_config",
    "user_browser_profile_dir",
    "user_config_path",
    "user_root",
    "user_seen_path",
]
