"""Grist's own HTTP API, used with the install's boot key to add people to the team site."""

import json
import urllib.error
import urllib.request
from email.message import Message
from typing import IO, Any

_UNEXPECTED = "Grist answered with unexpected data"


class GristApiError(Exception):
    """A call to Grist failed. The message never holds the key or a response body."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> urllib.request.Request | None:
        return None  # urllib then raises HTTPError(3xx): the boot key never follows a redirect


class GristTeam:
    def __init__(self, base_url: str, site: str, boot_key: str, role: str, timeout: float = 5.0) -> None:
        self._base = base_url
        self._site = site
        self._boot_key = boot_key
        self._role = role
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"x-boot-key": self._boot_key, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GristApiError(f"Grist answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GristApiError("Grist is not reachable") from None
        try:
            return json.loads(raw)
        except ValueError:
            raise GristApiError(_UNEXPECTED) from None

    def _members(self) -> list[Any]:
        answer = self._request("GET", f"/api/orgs/{self._site}/access")
        users = answer.get("users") if isinstance(answer, dict) else None
        if not isinstance(users, list):
            raise GristApiError(_UNEXPECTED)
        return users

    def check(self) -> None:
        self._members()

    def ensure_member(self, email: str) -> bool:
        """Add `email` to the team site with the configured role; an existing member is left alone."""
        wanted = email.lower()
        for entry in self._members():
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("email"), str)
                and entry["email"].lower() == wanted
                and entry.get("access") is not None
            ):
                return False
        self._request("PATCH", f"/api/orgs/{self._site}/access", {"delta": {"users": {email: self._role}}})
        return True
