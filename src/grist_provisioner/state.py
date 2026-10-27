"""The provisioner's own SQLite database: its keys, links and managed Grist keys."""

import hashlib
import secrets
import sqlite3
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS provisioning_keys (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    key_sha256 TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, revoked_at INTEGER);
CREATE TABLE IF NOT EXISTS links (external_id TEXT PRIMARY KEY, grist_user_id INTEGER NOT NULL UNIQUE,
    created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS managed_keys (grist_user_id INTEGER PRIMARY KEY, key_sha256 TEXT NOT NULL,
    expires_at INTEGER NOT NULL);
"""


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StateStore:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        if self._db.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0:
            self._db.execute("INSERT INTO schema_version (version) VALUES (1)")

    def add_key(self, name: str, now: int) -> tuple[int, str]:
        key = "gristprov_" + secrets.token_urlsafe(32)
        cur = self._db.execute(
            "INSERT INTO provisioning_keys (name, key_sha256, created_at) VALUES (?, ?, ?)",
            (name, sha256(key), now),
        )
        return int(cur.lastrowid or 0), key

    def list_keys(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT id, name, created_at, revoked_at FROM provisioning_keys ORDER BY id")
        return [{"id": r[0], "name": r[1], "created_at": r[2], "revoked_at": r[3]} for r in rows]

    def revoke_key(self, key_id: int, now: int) -> bool:
        cur = self._db.execute(
            "UPDATE provisioning_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, key_id)
        )
        return cur.rowcount == 1

    def key_valid(self, presented: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM provisioning_keys WHERE key_sha256 = ? AND revoked_at IS NULL", (sha256(presented),)
        ).fetchone()
        return row is not None

    def link_for(self, external_id: str) -> int | None:
        row = self._db.execute("SELECT grist_user_id FROM links WHERE external_id = ?", (external_id,)).fetchone()
        return None if row is None else int(row[0])

    def link_of_user(self, user_id: int) -> str | None:
        row = self._db.execute("SELECT external_id FROM links WHERE grist_user_id = ?", (user_id,)).fetchone()
        return None if row is None else str(row[0])

    def add_link(self, external_id: str, user_id: int, now: int) -> None:
        self._db.execute(
            "INSERT INTO links (external_id, grist_user_id, created_at) VALUES (?, ?, ?)", (external_id, user_id, now)
        )

    def drop_link(self, external_id: str) -> None:
        self._db.execute("DELETE FROM links WHERE external_id = ?", (external_id,))

    def managed(self, user_id: int) -> tuple[str, int] | None:
        """The (key sha256, expires_at) of the managed key issued to this user, if any."""
        row = self._db.execute(
            "SELECT key_sha256, expires_at FROM managed_keys WHERE grist_user_id = ?", (user_id,)
        ).fetchone()
        return None if row is None else (str(row[0]), int(row[1]))

    def set_managed(self, user_id: int, sha: str, expires_at: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO managed_keys (grist_user_id, key_sha256, expires_at) VALUES (?, ?, ?)",
            (user_id, sha, expires_at),
        )

    def drop_managed(self, user_id: int) -> None:
        self._db.execute("DELETE FROM managed_keys WHERE grist_user_id = ?", (user_id,))

    def expired_managed(self, now: int) -> list[tuple[int, str]]:
        rows = self._db.execute(
            "SELECT grist_user_id, key_sha256 FROM managed_keys WHERE expires_at <= ? ORDER BY grist_user_id", (now,)
        )
        return [(int(r[0]), str(r[1])) for r in rows]

    def count_links(self) -> int:
        return int(self._db.execute("SELECT count(*) FROM links").fetchone()[0])
