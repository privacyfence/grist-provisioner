"""End-to-end run of the provisioner against a real grist-core container (see real-grist.yml)."""

import os

import httpx
import pytest

from grist_provisioner.config import Settings
from grist_provisioner.griststore import GristStore
from grist_provisioner.service import Provisioner, ProvisionError
from grist_provisioner.state import StateStore, sha256

GRIST_URL = os.environ.get("GRIST_URL", "").rstrip("/")

pytestmark = [
    pytest.mark.real_grist,
    pytest.mark.skipif(not GRIST_URL, reason="GRIST_URL is not set"),
]


def _profile(key: str) -> httpx.Response:
    return httpx.get(f"{GRIST_URL}/api/profile/user", headers={"Authorization": f"Bearer {key}"})


def test_provisioner_against_real_grist(tmp_path):
    boot = {"x-boot-key": os.environ["GRIST_BOOT_KEY"]}
    people = ("alice", "bob", "carol")

    with httpx.Client(base_url=GRIST_URL, headers=boot, timeout=30) as grist:
        created = grist.post("/api/orgs", json={"name": "QA", "domain": "qa"})
        assert created.status_code == 200, created.text
        shared = grist.patch(
            f"/api/orgs/{created.json()}/access",
            json={"delta": {"users": {f"{p}@example.com": "editors" for p in people}}},
        )
        assert shared.status_code == 200, shared.text

        store = GristStore(os.environ["GRIST_HOME_DB"])
        state = StateStore(str(tmp_path / "state.sqlite3"))
        settings = Settings(
            grist_home_db=os.environ["GRIST_HOME_DB"],
            state_db=str(tmp_path / "state.sqlite3"),
            host="127.0.0.1",
            port=8485,
            admin_email="admin@example.com",
            default_email="admin@example.com",
            support_email="support@getgrist.com",
            allowed_domains=(),
        )
        provisioner = Provisioner(store, state, settings)
        store.check_schema()

        # alice: a managed key, revoked on release
        provisioner.upsert("alice", "alice@example.com")
        issued = provisioner.issue_key("alice")
        assert issued["managed"] is True
        profile = _profile(issued["key"])
        assert profile.status_code == 200, profile.text
        assert profile.json()["email"] == "alice@example.com"
        assert provisioner.release("alice")["revoked"] is True
        assert _profile(issued["key"]).status_code == 401

        # bob: an own key is adopted and survives release
        bob = provisioner.upsert("bob", "bob@example.com")
        assert store.set_key_if_empty(bob["user_id"], "b" * 40)
        adopted = provisioner.issue_key("bob")
        assert adopted["key"] == "b" * 40
        assert adopted["managed"] is False
        assert provisioner.release("bob")["revoked"] is False
        assert _profile("b" * 40).status_code == 200

        # carol: a disabled account gets no key
        carol = provisioner.upsert("carol", "carol@example.com")
        provisioner.issue_key("carol")
        disabled = grist.post(f"/api/users/{carol['user_id']}/disable")
        assert disabled.status_code == 200, disabled.text
        with pytest.raises(ProvisionError) as disabled_err:
            provisioner.issue_key("carol")
        assert (disabled_err.value.status, disabled_err.value.message) == (403, "account disabled")

        # the install admin and unknown people
        with pytest.raises(ProvisionError) as admin_err:
            provisioner.upsert("admin", "admin@example.com")
        assert admin_err.value.status == 409
        with pytest.raises(ProvisionError) as missing_err:
            provisioner.upsert("nobody", "nobody@example.com")
        assert missing_err.value.status == 404

        # alice again: the managed key expires
        again = provisioner.upsert("alice", "alice@example.com")
        reissued = provisioner.issue_key("alice")
        state.set_managed(again["user_id"], sha256(reissued["key"]), 1)
        assert provisioner.expire_once() == 1
        assert _profile(reissued["key"]).status_code == 401
