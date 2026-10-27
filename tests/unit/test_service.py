import json
import re
import sqlite3
from pathlib import Path

import pytest

from grist_provisioner.config import KEY_DAYS, Settings
from grist_provisioner.griststore import GristStore
from grist_provisioner.service import Provisioner, ProvisionError, key_id
from grist_provisioner.state import StateStore, sha256

SCHEMA = Path(__file__).parent.parent / "fixtures" / "grist_home_schema.sql"
NOW = 1_000_000
DAY = 86400


def settings(**over):
    values = {
        "grist_home_db": "",
        "state_db": "",
        "host": "127.0.0.1",
        "port": 8485,
        "admin_email": "boss@example.com",
        "default_email": "",
        "support_email": "support@getgrist.com",
        "allowed_domains": (),
    }
    values.update(over)
    return Settings(**values)


class Env:
    def __init__(self, tmp_path):
        self.raw = sqlite3.connect(tmp_path / "home.sqlite3")
        self.raw.executescript(SCHEMA.read_text())
        users = [
            (1, "alice@example.com", None, "login"),
            (2, "bob@example.com", "bobkey", "login"),
            (3, "svc@example.com", None, "service"),
            (4, "boss@example.com", None, "login"),
            (5, "support@getgrist.com", None, "login"),
            (6, "anon@getgrist.com", None, "login"),
            (7, "dee@example.com", None, "login"),
        ]
        for uid, email, key, kind in users:
            self.raw.execute("INSERT INTO users (id, name, api_key, type) VALUES (?, ?, ?, ?)", (uid, email, key, kind))
            self.raw.execute("INSERT INTO logins (email, display_email, user_id) VALUES (?, ?, ?)", (email, email, uid))
        self.raw.commit()
        self.store = GristStore(str(tmp_path / "home.sqlite3"))
        self.state = StateStore(str(tmp_path / "state.sqlite3"))
        self.now = NOW
        self.prov = self.make(settings())

    def make(self, conf):
        return Provisioner(self.store, self.state, conf, clock=lambda: self.now)

    def key_of(self, uid):
        return self.raw.execute("SELECT api_key FROM users WHERE id = ?", (uid,)).fetchone()[0]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.raw.close()
    e.store._db.close()
    e.state._db.close()


def status(call, *args):
    with pytest.raises(ProvisionError) as exc:
        call(*args)
    return exc.value.status, exc.value.message


def test_upsert_bad_external_id(env):
    assert status(env.prov.upsert, "bad id!", "alice@example.com") == (400, "bad external id")
    assert status(env.prov.upsert, "x" * 201, "alice@example.com") == (400, "bad external id")


@pytest.mark.parametrize("email", [None, 5, "a@", "ab", "a@b@c.com", "a" * 320 + "@x.com"])
def test_upsert_bad_email(env, email):
    assert status(env.prov.upsert, "u1", email) == (400, "bad email")


def test_upsert_domain_refused_and_allowed(env):
    prov = env.make(settings(allowed_domains=("corp.com",)))
    assert status(prov.upsert, "u1", "alice@example.com") == (403, "account email domain is not allowed")
    prov = env.make(settings(allowed_domains=("example.com",)))
    assert prov.upsert("u1", "Alice@EXAMPLE.com")["user_id"] == 1


def test_upsert_no_account_and_service_user(env):
    assert status(env.prov.upsert, "u1", "nobody@example.com") == (404, "no Grist account for this email")
    assert status(env.prov.upsert, "u1", "svc@example.com") == (404, "no Grist account for this email")


@pytest.mark.parametrize("email", ["boss@example.com", "support@getgrist.com", "anon@getgrist.com"])
def test_upsert_refuses_admin_addresses(env, email):
    assert status(env.prov.upsert, "u1", email) == (409, "account is a Grist admin; connect it by hand")


def test_upsert_refuses_default_email(env):
    prov = env.make(settings(admin_email="", default_email="Dee@Example.com"))
    assert status(prov.upsert, "u1", "dee@example.com")[0] == 409


def test_upsert_linked_elsewhere(env):
    env.prov.upsert("u1", "alice@example.com")
    msg = "account is linked to another external id or email"
    assert status(env.prov.upsert, "u1", "bob@example.com") == (409, msg)
    assert status(env.prov.upsert, "u2", "alice@example.com") == (409, msg)


def test_upsert_new_link_and_idempotent(env, capsys):
    out = env.prov.upsert("u1", "alice@example.com")
    assert out == {
        "external_id": "u1",
        "user_id": 1,
        "disabled": False,
        "has_key": False,
        "managed": False,
        "key_id": "",
        "expires_at": 0,
    }
    assert env.state.link_for("u1") == 1
    env.prov.upsert("u1", "alice@example.com")
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [x["event"] for x in lines] == ["link"]


def test_upsert_reports_managed_and_adopted(env):
    env.prov.upsert("u1", "alice@example.com")
    issued = env.prov.issue_key("u1")
    out = env.prov.upsert("u1", "alice@example.com")
    assert (out["has_key"], out["managed"], out["key_id"]) == (True, True, key_id(issued["key"]))
    assert out["expires_at"] == NOW + KEY_DAYS * DAY
    env.prov.upsert("u2", "bob@example.com")
    out = env.prov.upsert("u2", "bob@example.com")
    assert (out["has_key"], out["managed"], out["key_id"], out["expires_at"]) == (True, False, key_id("bobkey"), 0)


def test_issue_key_no_link(env):
    assert status(env.prov.issue_key, "u1") == (404, "not found")


def test_issue_key_missing_user_drops_link(env):
    env.state.add_link("ghost", 99, NOW)
    assert status(env.prov.issue_key, "ghost") == (404, "not found")
    assert env.state.link_for("ghost") is None


def test_issue_key_admin(env):
    env.state.add_link("u4", 4, NOW)
    assert status(env.prov.issue_key, "u4") == (409, "account is a Grist admin; connect it by hand")


def test_issue_key_disabled(env):
    env.prov.upsert("u1", "alice@example.com")
    env.raw.execute("UPDATE users SET disabled_at = '2026-01-01' WHERE id = 1")
    env.raw.commit()
    assert status(env.prov.issue_key, "u1") == (403, "account disabled")


def test_issue_key_new_managed(env):
    env.prov.upsert("u1", "alice@example.com")
    out = env.prov.issue_key("u1")
    assert re.fullmatch(r"[0-9a-f]{40}", out["key"])
    assert out["managed"] is True and out["expires_at"] == NOW + KEY_DAYS * DAY
    assert out["key_id"] == key_id(out["key"])
    assert env.key_of(1) == out["key"]
    assert env.state.managed(1) == (sha256(out["key"]), out["expires_at"])
    raw = sqlite3.connect(env.state._db.execute("PRAGMA database_list").fetchone()[2])
    dump = repr(raw.execute("SELECT * FROM managed_keys").fetchall()) + repr(
        raw.execute("SELECT * FROM links").fetchall()
    )
    raw.close()
    assert out["key"] not in dump


def test_issue_key_renews_same_key(env):
    env.prov.upsert("u1", "alice@example.com")
    first = env.prov.issue_key("u1")
    env.now += 10 * DAY
    again = env.prov.issue_key("u1")
    assert again["key"] == first["key"] and again["managed"] is True
    assert again["expires_at"] == env.now + KEY_DAYS * DAY
    assert env.state.managed(1) == (sha256(first["key"]), again["expires_at"])


def test_issue_key_adopts_own_key(env):
    env.prov.upsert("u2", "bob@example.com")
    env.state.set_managed(2, "stale-sha", NOW)
    out = env.prov.issue_key("u2")
    assert out == {"key": "bobkey", "managed": False, "key_id": key_id("bobkey"), "expires_at": 0}
    assert env.state.managed(2) is None


def test_issue_key_lost_race_adopts(env, monkeypatch):
    env.prov.upsert("u1", "alice@example.com")

    def lose(user_id, key):
        env.raw.execute("UPDATE users SET api_key = 'theirs' WHERE id = ?", (user_id,))
        env.raw.commit()
        return False

    monkeypatch.setattr(env.store, "set_key_if_empty", lose)
    out = env.prov.issue_key("u1")
    assert out["key"] == "theirs" and out["managed"] is False
    assert env.state.managed(1) is None


def test_issue_key_lost_race_without_key_gives_not_found(env, monkeypatch):
    env.prov.upsert("u1", "alice@example.com")
    monkeypatch.setattr(env.store, "set_key_if_empty", lambda user_id, key: False)
    assert status(env.prov.issue_key, "u1") == (404, "not found")
    assert env.state.link_for("u1") is None


def test_release_no_link(env):
    assert status(env.prov.release, "u1") == (404, "not found")


def test_release_revokes_managed_key(env):
    env.prov.upsert("u1", "alice@example.com")
    env.prov.issue_key("u1")
    assert env.prov.release("u1") == {"released": True, "revoked": True}
    assert env.key_of(1) is None
    assert env.state.managed(1) is None and env.state.link_for("u1") is None


def test_release_keeps_adopted_key(env):
    env.prov.upsert("u2", "bob@example.com")
    env.prov.issue_key("u2")
    assert env.prov.release("u2") == {"released": True, "revoked": False}
    assert env.key_of(2) == "bobkey"


def test_release_keeps_replaced_key(env):
    env.prov.upsert("u1", "alice@example.com")
    env.prov.issue_key("u1")
    env.raw.execute("UPDATE users SET api_key = 'mine' WHERE id = 1")
    env.raw.commit()
    assert env.prov.release("u1") == {"released": True, "revoked": False}
    assert env.key_of(1) == "mine"


def test_release_with_missing_user(env):
    env.state.add_link("ghost", 99, NOW)
    assert env.prov.release("ghost") == {"released": True, "revoked": False}


def test_expire_once_clears_only_expired_matching_keys(env):
    for ext, email in (("u1", "alice@example.com"), ("u7", "dee@example.com")):
        env.prov.upsert(ext, email)
        env.prov.issue_key(ext)
    env.prov.upsert("u2", "bob@example.com")
    env.state.set_managed(2, sha256("old-key"), NOW)  # expired, but Bob's key is another one
    env.state.set_managed(99, sha256("gone"), NOW)  # expired, user gone
    env.now += KEY_DAYS * DAY - 1
    env.prov.issue_key("u7")  # renewed: not expired at the end
    env.now += 2
    assert env.prov.expire_once() == 1
    assert env.key_of(1) is None
    assert env.key_of(2) == "bobkey"
    assert env.key_of(7) is not None
    assert env.state.managed(1) is None and env.state.managed(2) is None and env.state.managed(99) is None
    assert env.prov.expire_once() == 0


def test_audit_lines_have_no_email_or_key(env, capsys):
    env.prov.upsert("u1", "alice@example.com")
    key = env.prov.issue_key("u1")["key"]
    env.prov.upsert("u2", "bob@example.com")
    env.prov.issue_key("u2")
    env.prov.release("u1")
    env.prov.upsert("u7", "dee@example.com")
    env.prov.issue_key("u7")
    env.now += KEY_DAYS * DAY
    env.prov.expire_once()
    out = capsys.readouterr().out
    lines = [json.loads(x) for x in out.splitlines()]
    assert [x["event"] for x in lines] == ["link", "issue", "link", "adopt", "release", "link", "issue", "expire"]
    assert lines[4]["revoked"] is True and lines[7] == {**lines[7], "external_id": "u7", "revoked": True}
    assert "@" not in out and key not in out and "bobkey" not in out
