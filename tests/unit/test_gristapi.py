import io
import json
import urllib.error

import pytest

from grist_provisioner.gristapi import GristApiError, GristTeam, _NoRedirect


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return Response(answer if isinstance(answer, bytes) else json.dumps(answer).encode())


def team(*answers):
    t = GristTeam("http://grist:8484", "team", "BOOT", "editors")
    t._opener = FakeOpener(*answers)
    return t


def access(*users):
    return {"users": [{"email": e, "access": a} for e, a in users]}


def test_member_present_no_patch():
    t = team(access(("dave@example.com", "editors")))
    assert t.ensure_member("dave@example.com") is False
    assert len(t._opener.requests) == 1


def test_member_case_insensitive():
    t = team(access(("Dave@Example.com", "viewers")))
    assert t.ensure_member("dave@EXAMPLE.com") is False


def test_owner_present_no_patch():
    t = team(access(("erin@example.com", "owners")))
    assert t.ensure_member("erin@example.com") is False
    assert len(t._opener.requests) == 1


def test_access_null_patches():
    t = team(access(("dave@example.com", None)), {})
    assert t.ensure_member("dave@example.com") is True
    assert t._opener.requests[1].get_method() == "PATCH"


def test_absent_patches_exact_body():
    t = team({"users": ["junk", {"email": 5, "access": "owners"}, {"access": "owners"}]}, {})
    assert t.ensure_member("Dave@example.com") is True
    get, patch = t._opener.requests
    assert (get.get_method(), get.full_url, get.data) == ("GET", "http://grist:8484/api/orgs/team/access", None)
    assert patch.full_url == "http://grist:8484/api/orgs/team/access"
    assert json.loads(patch.data) == {"delta": {"users": {"Dave@example.com": "editors"}}}
    assert t._opener.timeouts == [5.0, 5.0]


def test_headers_carry_boot_key():
    t = team(access(), {})
    t.ensure_member("a@b.org")
    get, patch = t._opener.requests
    assert get.get_header("X-boot-key") == "BOOT" and get.get_header("Accept") == "application/json"
    assert get.get_header("Content-type") is None
    assert patch.get_header("X-boot-key") == "BOOT" and patch.get_header("Content-type") == "application/json"


def test_http_error_status():
    t = team(urllib.error.HTTPError("http://x", 403, "no", None, None))
    with pytest.raises(GristApiError) as err:
        t.check()
    assert str(err.value) == "Grist answered 403"


@pytest.mark.parametrize("exc", [urllib.error.URLError("BOOT secret"), OSError("BOOT")])
def test_url_error(exc):
    with pytest.raises(GristApiError) as err:
        team(exc).check()
    assert str(err.value) == "Grist is not reachable" and "BOOT" not in str(err.value)


def test_timeout():
    with pytest.raises(GristApiError, match="not reachable"):
        team(TimeoutError()).check()


def test_bad_json():
    with pytest.raises(GristApiError, match="unexpected data"):
        team(b"<html>").check()


@pytest.mark.parametrize("answer", [{}, {"users": "x"}, [1]])
def test_no_users_list(answer):
    with pytest.raises(GristApiError, match="unexpected data"):
        team(answer).check()


def test_check_ok():
    assert team(access()).check() is None


def test_no_redirect_returns_none():
    assert _NoRedirect().redirect_request(None, None, 302, "Found", None, "http://evil/") is None  # type: ignore[arg-type]


def test_opener_has_no_redirect_and_no_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    handlers = GristTeam("http://g:1", "t", "k", "editors")._opener.handlers
    assert any(isinstance(h, _NoRedirect) for h in handlers)
    # ProxyHandler({}) replaces the default handler that would read HTTP(S)_PROXY and registers nothing itself
    assert not any(h.__class__.__name__ == "ProxyHandler" for h in handlers)
