"""Reads and writes users.api_key in Grist's home database (SQLite)."""

import sqlite3
from dataclasses import dataclass, field

_SCHEMA_MESSAGE = (
    "Grist's home database does not have the users and logins columns this provisioner expects "
    "(it supports grist-core 1.7). Check GRIST_HOME_DB, or update the provisioner."
)
_REQUIRED = {"users": ("id", "api_key", "disabled_at", "type"), "logins": ("email", "user_id")}
_SELECT = "SELECT u.id, l.email, u.disabled_at, u.api_key, u.type FROM logins l JOIN users u ON u.id = l.user_id WHERE "


@dataclass(frozen=True)
class GristUser:
    id: int
    email: str
    disabled: bool
    api_key: str | None = field(repr=False)
    type: str


class SchemaError(Exception):
    """Grist's home database is not the shape this provisioner supports."""


class GristStore:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)

    def check_schema(self) -> None:
        for table, columns in _REQUIRED.items():
            have = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            if not set(columns) <= have:
                raise SchemaError(_SCHEMA_MESSAGE)

    def _one(self, where: str, value: object) -> GristUser | None:
        row = self._db.execute(_SELECT + where + " LIMIT 1", (value,)).fetchone()
        if row is None:
            return None
        return GristUser(id=row[0], email=row[1], disabled=row[2] is not None, api_key=row[3], type=row[4])

    def find_by_email(self, email: str) -> GristUser | None:
        return self._one("l.email = ?", email.lower())

    def find_by_id(self, user_id: int) -> GristUser | None:
        return self._one("u.id = ?", user_id)

    def _update(self, sql: str, params: tuple[object, ...]) -> bool:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            changed = self._db.execute(sql, params).rowcount == 1
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return changed

    def set_key_if_empty(self, user_id: int, key: str) -> bool:
        return self._update(
            "UPDATE users SET api_key = ? WHERE id = ? AND (api_key IS NULL OR api_key = '')", (key, user_id)
        )

    def clear_key_if(self, user_id: int, key: str) -> bool:
        return self._update("UPDATE users SET api_key = NULL WHERE id = ? AND api_key = ?", (user_id, key))
