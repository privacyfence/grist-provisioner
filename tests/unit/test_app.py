import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from grist_provisioner.app import create_app
from grist_provisioner.config import Settings
from grist_provisioner.griststore import GristStore
from grist_provisioner.service import Provisioner
from grist_provisioner.state import StateStore

SCHEMA = Path(__file__).parent.parent / "fixtures" / "grist_home_schema.sql"


@pytest.fixture
def api(tmp_path):
    raw = sqlite3.connect(tmp_path / "home.sqlite3")
    raw.executescript(SCHEMA.read_text())
    for uid, email in ((1, "alice@example.com"), (2, "boss@example.com")):
        raw.execute("INSERT INTO users (id, name) VALUES (?, ?)", (uid, email))
        raw.execute("INSERT INTO logins (email, display_email, user_id) VALUES (?, ?, ?)", (email, email, uid))
    raw.commit()
    raw.close()
    conf = Settings("", "", "127.0.0.1", 8485, "boss@example.com", "", "support@getgrist.com", ())
    store = GristStore(str(tmp_path / "home.sqlite3"))
    state = StateStore(str(tmp_path / "state.sqlite3"))
    key_id, key = state.add_key("pf", 1)
    client = TestClient(create_app(Provisioner(store, state, conf), state))
    client.headers["Authorization"] = f"Bearer {key}"
    client.key_id = key_id
    client.state = state
    yield client
    store._db.close()
    state._db.close()


PATH = "/api/provisioning/accounts/u1"


def test_revoked_key_gives_401(api):
    api.state.revoke_key(api.key_id, 2)
    r = api.get("/api/provisioning/self")
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})
    for method, path in (("PUT", PATH), ("POST", PATH + "/key"), ("DELETE", PATH)):
        assert api.request(method, path, json={"email": "a@b.c"}).status_code == 401


def test_missing_and_bad_keys_give_401(api):
    api.headers.pop("Authorization")
    r = api.get("/api/provisioning/self")
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})
    for value in ("Bearer nope", "Basic abc"):
        assert api.get("/api/provisioning/self", headers={"Authorization": value}).status_code == 401


def test_self(api):
    r = api.get("/api/provisioning/self")
    assert (r.status_code, r.json()) == (200, {"name": "grist-provisioner", "accounts": 0})


def test_full_flow(api):
    r = api.put(PATH, json={"email": "alice@example.com"})
    assert r.status_code == 200
    assert r.json() == {
        "external_id": "u1", "user_id": 1, "disabled": False, "has_key": False,
        "managed": False, "key_id": "", "expires_at": 0,
    }  # fmt: skip
    r = api.post(PATH + "/key")
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"key", "managed", "key_id", "expires_at"} and body["managed"] is True
    assert api.get("/api/provisioning/self").json()["accounts"] == 1
    r = api.delete(PATH)
    assert (r.status_code, r.json()) == (200, {"released": True, "revoked": True})


def test_errors_use_error_json(api):
    r = api.post(PATH + "/key")
    assert (r.status_code, r.json()) == (404, {"error": "not found"})
    r = api.put("/api/provisioning/accounts/u2", json={"email": "boss@example.com"})
    assert (r.status_code, r.json()) == (409, {"error": "account is a Grist admin; connect it by hand"})
    r = api.put(PATH, json={})
    assert (r.status_code, r.json()) == (400, {"error": "bad email"})


@pytest.mark.parametrize("content", [b"[1]", b'"x"', b"not json", b"", b"null"])
def test_non_object_body_is_400(api, content):
    r = api.put(PATH, content=content)
    assert (r.status_code, r.json()) == (400, {"error": "bad request"})


def test_unknown_route_is_plain_text_404(api):
    r = api.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "Not Found"


def test_healthz_needs_no_key(api):
    api.headers.pop("Authorization")
    r = api.get("/healthz")
    assert (r.status_code, r.text) == (200, "ok")
