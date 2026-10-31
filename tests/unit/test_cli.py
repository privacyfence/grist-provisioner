import sqlite3
import threading
from pathlib import Path

import pytest

from grist_provisioner import cli
from grist_provisioner.state import StateStore

SCHEMA = Path(__file__).parent.parent / "fixtures" / "grist_home_schema.sql"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home.sqlite3"
    raw = sqlite3.connect(home)
    raw.executescript(SCHEMA.read_text())
    raw.execute("INSERT INTO users (id, name) VALUES (1, 'a'), (2, 'b')")
    raw.commit()
    raw.close()
    monkeypatch.setenv("GRIST_HOME_DB", str(home))
    monkeypatch.setenv("PROVISIONER_STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("GRIST_ADMIN_EMAIL", "boss@example.com")
    monkeypatch.delenv("GRIST_DEFAULT_EMAIL", raising=False)
    monkeypatch.setenv("PROVISIONER_PORT", "9999")
    monkeypatch.setenv("PROVISIONER_HOST", "127.0.0.9")
    return tmp_path


def break_schema(tmp_path):
    raw = sqlite3.connect(tmp_path / "home.sqlite3")
    raw.execute("DROP TABLE logins")
    raw.commit()
    raw.close()


def test_create_key_prints_it_once(env, capsys):
    assert cli.main(["create-key", "--name", "privacyfence"]) == 0
    out, err = capsys.readouterr()
    key = out.strip()
    assert key.startswith("gristprov_") and "\n" not in key
    assert err == "Store this key in PrivacyFence's environment; it is not shown again.\n"
    state = StateStore(str(env / "state.sqlite3"))
    assert state.key_valid(key)


def test_list_and_revoke_keys(env, capsys):
    cli.main(["create-key", "--name", "one"])
    cli.main(["create-key", "--name", "two"])
    capsys.readouterr()
    assert cli.main(["revoke-key", "1"]) == 0
    assert cli.main(["list-keys"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("1\tone\tcreated ") and not lines[0].endswith("revoked -")
    assert lines[1].startswith("2\ttwo\tcreated ") and lines[1].endswith("revoked -")


def test_revoke_unknown_key_fails(env, capsys):
    assert cli.main(["revoke-key", "9"]) == 2
    assert "no active key with id 9" in capsys.readouterr().err


def test_check_ok(env, capsys):
    assert cli.main(["check"]) == 0
    assert capsys.readouterr().out == "ok: 2 Grist users, 0 linked\n"


def test_check_bad_schema(env, capsys):
    break_schema(env)
    assert cli.main(["check"]) == 2
    assert "does not have the users and logins columns" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["serve", "check"])
def test_exit_2_without_admin_emails(env, monkeypatch, capsys, command):
    monkeypatch.delenv("GRIST_ADMIN_EMAIL")
    assert cli.main([command]) == 2
    assert "Set GRIST_ADMIN_EMAIL or GRIST_DEFAULT_EMAIL" in capsys.readouterr().err


def test_serve_bad_schema_exits_2(env, monkeypatch, capsys):
    break_schema(env)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: pytest.fail("must not start"))
    assert cli.main(["serve"]) == 2
    assert "does not have the users and logins columns" in capsys.readouterr().err


def test_serve_starts_expiry_thread_and_uvicorn(env, monkeypatch):
    seen = {}
    ran = threading.Event()

    def fake_loop(provisioner, stop):
        seen["provisioner"] = provisioner
        seen["stop"] = stop
        ran.set()

    def fake_run(app, host, port):
        assert ran.wait(5)
        seen.update(app=app, host=host, port=port, stopped_at_run=seen["stop"].is_set())

    monkeypatch.setattr(cli, "_expiry_loop", fake_loop)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    assert cli.main(["serve"]) == 0
    assert (seen["host"], seen["port"]) == ("127.0.0.9", 9999)
    assert seen["app"] is not None and seen["stopped_at_run"] is False
    assert seen["stop"].is_set()


class FakeProvisioner:
    def __init__(self, stop, fail_first=False):
        self.calls = 0
        self.stop = stop
        self.fail_first = fail_first

    def expire_once(self):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("database is locked")
        if self.calls == 2:
            self.stop.set()
        return 0


def test_expiry_loop_runs_logs_failure_and_stops(monkeypatch, capsys):
    stop = threading.Event()
    waits = []
    monkeypatch.setattr(stop, "wait", lambda seconds: waits.append(seconds))
    prov = FakeProvisioner(stop, fail_first=True)
    cli._expiry_loop(prov, stop)
    assert prov.calls == 2
    assert waits == [3600, 3600]
    assert "expiry pass failed (will retry): database is locked" in capsys.readouterr().err


def test_expiry_loop_returns_at_once_when_stopped(monkeypatch):
    stop = threading.Event()
    stop.set()
    prov = FakeProvisioner(stop)
    cli._expiry_loop(prov, stop)
    assert prov.calls == 0


KEY = "gristprov_" + "k" * 40


def no_uvicorn(monkeypatch):
    ran = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: ran.append(1))
    return ran


def test_serve_registers_key_file(env, monkeypatch, capsys):
    ran = no_uvicorn(monkeypatch)
    (env / "key").write_text(KEY + "\n")
    monkeypatch.setenv("PROVISIONER_KEY_FILE", str(env / "key"))
    assert cli.main(["serve"]) == 0
    assert 'provisioning key "key-file" registered from PROVISIONER_KEY_FILE' in capsys.readouterr().err
    assert StateStore(str(env / "state.sqlite3")).key_valid(KEY)
    assert cli.main(["serve"]) == 0  # second start: already registered, silent
    assert capsys.readouterr().err == ""
    assert len(ran) == 2


@pytest.mark.parametrize("content", [None, "nope", "gristprov_short"])
def test_serve_bad_key_file_exit_2(env, monkeypatch, capsys, content):
    ran = no_uvicorn(monkeypatch)
    if content is not None:
        (env / "key").write_text(content)
    monkeypatch.setenv("PROVISIONER_KEY_FILE", str(env / "key"))
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert (
        "PROVISIONER_KEY_FILE cannot be read: " if content is None else "must hold a key that starts with gristprov_"
    ) in err
    assert ran == []


def test_serve_revoked_key_file_exit_2(env, monkeypatch, capsys):
    ran = no_uvicorn(monkeypatch)
    (env / "key").write_text(KEY)
    monkeypatch.setenv("PROVISIONER_KEY_FILE", str(env / "key"))
    assert cli.main(["serve"]) == 0
    StateStore(str(env / "state.sqlite3")).revoke_key(1, 5)
    capsys.readouterr()
    assert cli.main(["serve"]) == 2
    assert "PROVISIONER_KEY_FILE holds a revoked key; put a new key in the file" in capsys.readouterr().err
    assert len(ran) == 1


def team_env(env, monkeypatch):
    (env / "boot").write_text("BOOT")
    monkeypatch.setenv("PROVISIONER_GRIST_URL", "http://grist:8484")
    monkeypatch.setenv("PROVISIONER_GRIST_TEAM_SITE", "team")
    monkeypatch.setenv("PROVISIONER_GRIST_BOOT_KEY_FILE", str(env / "boot"))
    monkeypatch.setenv("PROVISIONER_ALLOWED_DOMAINS", "example.com")


def test_check_with_team_ok(env, monkeypatch, capsys):
    team_env(env, monkeypatch)
    monkeypatch.setattr(cli.GristTeam, "check", lambda self: None)
    assert cli.main(["check"]) == 0
    assert capsys.readouterr().out == "ok: 2 Grist users, 0 linked\nteam site: team\n"


def test_check_with_team_error(env, monkeypatch, capsys):
    team_env(env, monkeypatch)

    def boom(self):
        raise cli.GristApiError("Grist answered 401")

    monkeypatch.setattr(cli.GristTeam, "check", boom)
    assert cli.main(["check"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err == "Grist answered 401\n"


def test_serve_builds_team(env, monkeypatch):
    team_env(env, monkeypatch)
    seen = {}
    monkeypatch.setattr(cli, "Provisioner", lambda *a, **k: seen.update(k) or object())
    monkeypatch.setattr(cli.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: None})())
    monkeypatch.setattr(cli, "create_app", lambda *a: None)
    no_uvicorn(monkeypatch)
    assert cli.main(["serve"]) == 0
    assert isinstance(seen["team"], cli.GristTeam)
