"""Local user accounts (SQLite + scrypt password hashes)."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "users.sqlite"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


@dataclass(frozen=True)
class User:
    id: int
    username: str


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, hexd = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return secrets.compare_digest(digest.hex(), hexd)
    except (ValueError, AttributeError):
        return False


def register_user(username: str, password: str) -> tuple[bool, str]:
    init_db()
    name = (username or "").strip()
    if not _USERNAME_RE.match(name):
        return False, "Username: 3–32 chars (letters, numbers, _ . -)"
    if len(password or "") < 8:
        return False, "Password: at least 8 characters"
    pw_hash = _hash_password(password)
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (name, pw_hash),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return False, "Username already taken"
    return True, "Account created — you can log in."


def authenticate(username: str, password: str) -> User | None:
    init_db()
    name = (username or "").strip()
    if not name:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return None
    return User(id=int(row["id"]), username=str(row["username"]))
