import hashlib
import sqlite3

import pytest

from grist_provisioner.state import RevokedKeyError, StateStore


@pytest.fixture
def state(tmp_path):
    return StateStore(str(tmp_path / "state.sqlite3"))


def test_add_key_stores_only_the_hash(state, tmp_path):
    key_id, key = state.add_key("privacyfence", 100)
    assert key.startswith("gristprov_")
    raw = sqlite3.connect(tmp_path / "state.sqlite3")
    rows = raw.execute("SELECT * FROM provisioning_keys").fetchall()
    raw.close()
    assert rows == [(key_id, "privacyfence", hashlib.sha256(key.encode()).hexdigest(), 100, None)]
    assert key not in repr(rows)


def test_reopen_keeps_schema_version(tmp_path):
    StateStore(str(tmp_path / "s.db"))
    StateStore(str(tmp_path / "s.db"))
    raw = sqlite3.connect(tmp_path / "s.db")
    count = raw.execute("SELECT count(*) FROM schema_version").fetchone()[0]
    raw.close()
    assert count == 1


def test_key_valid_and_revoke(state):
    key_id, key = state.add_key("a", 1)
    assert state.key_valid(key) is True
    assert state.key_valid("gristprov_unknown") is False
    assert state.revoke_key(key_id, 5) is True
    assert state.key_valid(key) is False
    assert state.revoke_key(key_id, 6) is False
    assert state.revoke_key(999, 6) is False


def test_list_keys(state):
    i1, _ = state.add_key("a", 1)
    i2, _ = state.add_key("b", 2)
    state.revoke_key(i1, 3)
    assert state.list_keys() == [
        {"id": i1, "name": "a", "created_at": 1, "revoked_at": 3},
        {"id": i2, "name": "b", "created_at": 2, "revoked_at": None},
    ]


def test_links_round_trip(state):
    assert state.link_for("e1") is None and state.link_of_user(7) is None
    state.add_link("e1", 7, 10)
    assert state.link_for("e1") == 7
    assert state.link_of_user(7) == "e1"
    state.drop_link("e1")
    assert state.link_for("e1") is None


def test_links_are_unique_both_ways(state):
    state.add_link("e1", 7, 10)
    with pytest.raises(sqlite3.IntegrityError):
        state.add_link("e2", 7, 11)
    with pytest.raises(sqlite3.IntegrityError):
        state.add_link("e1", 8, 11)


def test_managed_round_trip(state):
    assert state.managed(7) is None
    state.set_managed(7, "sha1", 100)
    assert state.managed(7) == ("sha1", 100)
    state.set_managed(7, "sha2", 200)
    assert state.managed(7) == ("sha2", 200)
    state.drop_managed(7)
    assert state.managed(7) is None


def test_expired_managed(state):
    state.set_managed(1, "a", 100)
    state.set_managed(2, "b", 200)
    state.set_managed(3, "c", 300)
    assert state.expired_managed(200) == [(1, "a"), (2, "b")]
    assert state.expired_managed(50) == []


def test_count_links(state):
    assert state.count_links() == 0
    state.add_link("e1", 1, 1)
    state.add_link("e2", 2, 1)
    assert state.count_links() == 2


KEY = "gristprov_" + "k" * 40


def rows(tmp_path):
    raw = sqlite3.connect(tmp_path / "state.sqlite3")
    try:
        return raw.execute("SELECT name, key_sha256, revoked_at FROM provisioning_keys ORDER BY id").fetchall()
    finally:
        raw.close()


def test_ensure_key_registers(state, tmp_path):
    assert state.ensure_key("key-file", KEY, 7) is True
    assert state.key_valid(KEY)
    assert rows(tmp_path) == [("key-file", hashlib.sha256(KEY.encode()).hexdigest(), None)]


def test_ensure_key_idempotent(state):
    assert state.ensure_key("key-file", KEY, 7) is True
    assert state.ensure_key("key-file", KEY, 8) is False
    assert state.key_valid(KEY)
    assert len(state.list_keys()) == 1


def test_ensure_key_rotates_same_name_only(state):
    _, other = state.add_key("privacyfence", 1)
    state.ensure_key("key-file", KEY, 7)
    new = KEY + "x"
    assert state.ensure_key("key-file", new, 9) is True
    assert not state.key_valid(KEY) and state.key_valid(new) and state.key_valid(other)


def test_ensure_key_revoked_raises(state):
    state.ensure_key("key-file", KEY, 7)
    state.revoke_key(1, 8)
    with pytest.raises(RevokedKeyError):
        state.ensure_key("key-file", KEY, 9)
    assert not state.key_valid(KEY)
    state.ensure_key("other", "gristprov_" + "z" * 40, 9)  # the transaction was rolled back, so the db still works
