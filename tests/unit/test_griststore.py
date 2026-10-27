import sqlite3
from pathlib import Path

import pytest

from grist_provisioner.griststore import GristStore, GristUser, SchemaError

SCHEMA = Path(__file__).parent.parent / "fixtures" / "grist_home_schema.sql"


def make_db(path, schema=None):
    db = sqlite3.connect(path)
    db.executescript(schema if schema is not None else SCHEMA.read_text())
    return db


@pytest.fixture
def raw(tmp_path):
    db = make_db(tmp_path / "home.sqlite3")
    db.execute("INSERT INTO users (id, name, api_key) VALUES (1, 'Alice', NULL), (2, 'Bob', 'bobkey'), (3, 'Cy', '')")
    db.execute("UPDATE users SET disabled_at = '2026-01-01' WHERE id = 3")
    db.execute(
        "INSERT INTO logins (email, display_email, user_id) VALUES "
        "('alice@example.com', 'Alice@Example.com', 1), ('bob@example.com', 'bob@example.com', 2), "
        "('cy@example.com', 'cy@example.com', 3)"
    )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def store(raw, tmp_path):
    return GristStore(str(tmp_path / "home.sqlite3"))


def key_of(raw, uid):
    return raw.execute("SELECT api_key FROM users WHERE id = ?", (uid,)).fetchone()[0]


def test_check_schema_passes(store):
    store.check_schema()


@pytest.mark.parametrize(
    "table,column",
    [
        ("users", "id"),
        ("users", "api_key"),
        ("users", "disabled_at"),
        ("users", "type"),
        ("logins", "email"),
        ("logins", "user_id"),
    ],
)
def test_check_schema_missing_column(tmp_path, table, column):
    users = {"id": "id INTEGER", "api_key": "api_key TEXT", "disabled_at": "disabled_at DATETIME", "type": "type TEXT"}
    logins = {"email": "email TEXT", "user_id": "user_id INTEGER"}
    cols = {"users": dict(users), "logins": dict(logins)}
    del cols[table][column]
    ddl = "".join(f"CREATE TABLE {t} ({', '.join(c.values())});" for t, c in cols.items())
    make_db(tmp_path / "bad.sqlite3", ddl).close()
    store = GristStore(str(tmp_path / "bad.sqlite3"))
    with pytest.raises(SchemaError, match="does not have the users and logins columns"):
        store.check_schema()


def test_find_by_email_is_case_insensitive(store):
    user = store.find_by_email("ALICE@Example.com")
    assert user == GristUser(id=1, email="alice@example.com", disabled=False, api_key=None, type="login")


def test_find_by_email_missing(store):
    assert store.find_by_email("nobody@example.com") is None


def test_find_by_id(store):
    user = store.find_by_id(3)
    assert user is not None and user.email == "cy@example.com" and user.disabled and user.api_key == ""
    assert store.find_by_id(99) is None


@pytest.mark.parametrize("uid", [1, 3])
def test_set_key_if_empty_on_null_and_empty(store, raw, uid):
    assert store.set_key_if_empty(uid, "newkey") is True
    assert key_of(raw, uid) == "newkey"


def test_set_key_if_empty_keeps_existing(store, raw):
    assert store.set_key_if_empty(2, "newkey") is False
    assert key_of(raw, 2) == "bobkey"


def test_clear_key_if(store, raw):
    assert store.clear_key_if(2, "other") is False
    assert key_of(raw, 2) == "bobkey"
    assert store.clear_key_if(2, "bobkey") is True
    assert key_of(raw, 2) is None


def test_failed_update_rolls_back(store):
    with pytest.raises(sqlite3.Error):
        store._update("UPDATE nope SET x = 1", ())
    assert store.set_key_if_empty(1, "k") is True  # no transaction left open


def test_repr_has_no_key(store):
    user = store.find_by_id(2)
    assert user is not None and "bobkey" not in repr(user)
