"""Command line: serve, create-key, list-keys, revoke-key, check."""

import argparse
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Sequence

import uvicorn

from grist_provisioner.app import create_app
from grist_provisioner.config import EXPIRE_INTERVAL_SECONDS, ConfigError, Settings
from grist_provisioner.griststore import GristStore, SchemaError
from grist_provisioner.service import Provisioner
from grist_provisioner.state import StateStore


def _expiry_loop(provisioner: Provisioner, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            provisioner.expire_once()
        except Exception as exc:  # noqa: BLE001 - the loop must survive any failure
            print(f"expiry pass failed (will retry): {exc}", file=sys.stderr)
        stop.wait(EXPIRE_INTERVAL_SECONDS)


def _state_path() -> str:
    return os.environ.get("PROVISIONER_STATE_DB", "/data/provisioner.sqlite3")


def _fail(message: object) -> int:
    print(message, file=sys.stderr)
    return 2


def _open(settings: Settings) -> tuple[GristStore, StateStore]:
    store = GristStore(settings.grist_home_db)
    store.check_schema()
    return store, StateStore(settings.state_db)


def _serve() -> int:
    try:
        settings = Settings.from_env(os.environ)
        store, state = _open(settings)
    except (ConfigError, SchemaError) as exc:
        return _fail(exc)
    provisioner = Provisioner(store, state, settings)
    stop = threading.Event()
    threading.Thread(target=_expiry_loop, args=(provisioner, stop), daemon=True).start()
    try:
        uvicorn.run(create_app(provisioner, state), host=settings.host, port=settings.port)
    finally:
        stop.set()
    return 0


def _check() -> int:
    try:
        settings = Settings.from_env(os.environ)
        _, state = _open(settings)
    except (ConfigError, SchemaError) as exc:
        return _fail(exc)
    grist = sqlite3.connect(f"file:{settings.grist_home_db}?mode=ro", uri=True)
    try:
        users = grist.execute("SELECT count(*) FROM users").fetchone()[0]
    finally:
        grist.close()
    print(f"ok: {users} Grist users, {state.count_links()} linked")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grist-provisioner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    create = sub.add_parser("create-key")
    create.add_argument("--name", required=True)
    sub.add_parser("list-keys")
    revoke = sub.add_parser("revoke-key")
    revoke.add_argument("id", type=int)
    sub.add_parser("check")
    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve()
    if args.command == "check":
        return _check()
    state = StateStore(_state_path())
    now = int(time.time())
    if args.command == "create-key":
        _, key = state.add_key(args.name, now)
        print(key)
        print("Store this key in PrivacyFence's environment; it is not shown again.", file=sys.stderr)
    elif args.command == "list-keys":
        for row in state.list_keys():
            print(f"{row['id']}\t{row['name']}\tcreated {row['created_at']}\trevoked {row['revoked_at'] or '-'}")
    elif not state.revoke_key(args.id, now):
        return _fail(f"no active key with id {args.id}")
    return 0
