"""The provisioning rules: link accounts, issue, release and expire Grist API keys."""

import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from grist_provisioner.config import KEY_DAYS, Settings
from grist_provisioner.gristapi import GristApiError, GristTeam
from grist_provisioner.griststore import GristStore, GristUser
from grist_provisioner.state import StateStore, sha256

_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._@-]{1,200}$")
_SPECIAL = ("anon@getgrist.com", "everyone@getgrist.com", "thumbnail@getgrist.com")
_NOT_FOUND = "not found"
_ADMIN = "account is a Grist admin; connect it by hand"


class ProvisionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def key_id(key: str) -> str:
    return sha256(key)[:12]


class Provisioner:
    def __init__(
        self,
        store: GristStore,
        state: StateStore,
        settings: Settings,
        clock: Callable[[], float] = time.time,
        team: GristTeam | None = None,
    ) -> None:
        self._store = store
        self._state = state
        self._settings = settings
        self._clock = clock
        self._team = team
        self._lock = threading.Lock()
        refused = (settings.admin_email, settings.default_email, settings.support_email, *_SPECIAL)
        self._refused = {e.lower() for e in refused if e}

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _audit(event: str, external_id: str, user_id: int, revoked: bool | None = None) -> None:
        line: dict[str, Any] = {"ts": int(time.time()), "event": event, "external_id": external_id}
        line["grist_user_id"] = user_id
        if revoked is not None:
            line["revoked"] = revoked
        print(json.dumps(line), flush=True)

    def _is_managed(self, user: GristUser) -> tuple[str, int] | None:
        row = self._state.managed(user.id)
        if row is not None and user.api_key and row[0] == sha256(user.api_key):
            return row
        return None

    def _check_admin(self, user: GristUser) -> None:
        if user.email.lower() in self._refused:
            raise ProvisionError(409, _ADMIN)

    def upsert(self, external_id: str, email: object) -> dict[str, Any]:
        with self._lock:
            if not _EXTERNAL_ID.match(external_id):
                raise ProvisionError(400, "bad external id")
            if not (isinstance(email, str) and 3 <= len(email) <= 320 and email.count("@") == 1):
                raise ProvisionError(400, "bad email")
            domains = self._settings.allowed_domains
            if domains and email.rsplit("@", 1)[1].lower() not in domains:
                raise ProvisionError(403, "account email domain is not allowed")
            if email.lower() in self._refused:
                raise ProvisionError(409, _ADMIN)
            if self._team is not None and self._state.link_for(external_id) is None:
                try:
                    added = self._team.ensure_member(email)
                except GristApiError:
                    raise ProvisionError(502, "could not add the account to the Grist team site") from None
                if added:
                    line = {"ts": int(time.time()), "event": "member", "external_id": external_id}
                    print(json.dumps(line), flush=True)
            user = self._store.find_by_email(email)
            if user is None or user.type != "login":
                raise ProvisionError(404, "no Grist account for this email")
            self._check_admin(user)
            linked_user = self._state.link_for(external_id)
            linked_id = self._state.link_of_user(user.id)
            if (linked_user is not None and linked_user != user.id) or (
                linked_id is not None and linked_id != external_id
            ):
                raise ProvisionError(409, "account is linked to another external id or email")
            if linked_user is None:
                self._state.add_link(external_id, user.id, self._now())
                self._audit("link", external_id, user.id)
            managed = self._is_managed(user)
            return {
                "external_id": external_id,
                "user_id": user.id,
                "disabled": user.disabled,
                "has_key": bool(user.api_key),
                "managed": managed is not None,
                "key_id": key_id(user.api_key) if user.api_key else "",
                "expires_at": managed[1] if managed else 0,
            }

    def issue_key(self, external_id: str) -> dict[str, Any]:
        with self._lock:
            user_id = self._state.link_for(external_id)
            if user_id is None:
                raise ProvisionError(404, _NOT_FOUND)
            user = self._store.find_by_id(user_id)
            if user is None:
                self._state.drop_link(external_id)
                raise ProvisionError(404, _NOT_FOUND)
            self._check_admin(user)
            if user.disabled:
                raise ProvisionError(403, "account disabled")
            if not user.api_key:
                key = secrets.token_hex(20)
                if self._store.set_key_if_empty(user.id, key):
                    expires_at = self._now() + KEY_DAYS * 86400
                    self._state.set_managed(user.id, sha256(key), expires_at)
                    self._audit("issue", external_id, user.id)
                    return {"key": key, "managed": True, "key_id": key_id(key), "expires_at": expires_at}
                user = self._reread(external_id, user.id)
            current = user.api_key or ""
            managed = self._is_managed(user)
            if managed is not None:
                expires_at = self._now() + KEY_DAYS * 86400
                self._state.set_managed(user.id, managed[0], expires_at)
                self._audit("issue", external_id, user.id)
                return {"key": current, "managed": True, "key_id": key_id(current), "expires_at": expires_at}
            self._state.drop_managed(user.id)
            self._audit("adopt", external_id, user.id)
            return {"key": current, "managed": False, "key_id": key_id(current), "expires_at": 0}

    def _reread(self, external_id: str, user_id: int) -> GristUser:
        user = self._store.find_by_id(user_id)
        if user is None or not user.api_key:
            self._state.drop_link(external_id)
            raise ProvisionError(404, _NOT_FOUND)
        return user

    def release(self, external_id: str) -> dict[str, Any]:
        with self._lock:
            user_id = self._state.link_for(external_id)
            if user_id is None:
                raise ProvisionError(404, _NOT_FOUND)
            user = self._store.find_by_id(user_id)
            revoked = False
            if user is not None and user.api_key and self._is_managed(user) is not None:
                revoked = self._store.clear_key_if(user.id, user.api_key)
            self._state.drop_managed(user_id)
            self._state.drop_link(external_id)
            self._audit("release", external_id, user_id, revoked)
            return {"released": True, "revoked": revoked}

    def expire_once(self) -> int:
        with self._lock:
            cleared = 0
            for user_id, sha in self._state.expired_managed(self._now()):
                user = self._store.find_by_id(user_id)
                revoked = False
                if user is not None and user.api_key and sha256(user.api_key) == sha:
                    revoked = self._store.clear_key_if(user.id, user.api_key)
                self._state.drop_managed(user_id)
                if revoked:
                    cleared += 1
                    self._audit("expire", self._state.link_of_user(user_id) or "", user_id, True)
            return cleared
