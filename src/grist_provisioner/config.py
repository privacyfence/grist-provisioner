"""Settings read from the environment."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

KEY_DAYS = 90
EXPIRE_INTERVAL_SECONDS = 3600
_SITE = re.compile(r"^[a-z0-9-]{1,100}$")
_ROLES = ("editors", "viewers", "members")


class ConfigError(Exception):
    """The environment does not describe a usable setup."""


@dataclass(frozen=True)
class Settings:
    grist_home_db: str
    state_db: str
    host: str
    port: int
    admin_email: str
    default_email: str
    support_email: str
    allowed_domains: tuple[str, ...]
    key_file: str = ""
    key_name: str = "key-file"
    grist_url: str = ""
    team_site: str = ""
    boot_key: str = field(default="", repr=False)
    team_role: str = "editors"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        try:
            port = int(environ.get("PROVISIONER_PORT", "8485"))
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ConfigError("PROVISIONER_PORT must be a number from 1 to 65535")
        admin_email = environ.get("GRIST_ADMIN_EMAIL", "").strip()
        default_email = environ.get("GRIST_DEFAULT_EMAIL", "").strip()
        if not admin_email and not default_email:
            raise ConfigError(
                "Set GRIST_ADMIN_EMAIL or GRIST_DEFAULT_EMAIL to the same value as Grist's, "
                "so the install admin is never provisioned."
            )
        parts = environ.get("PROVISIONER_ALLOWED_DOMAINS", "").split(",")
        domains = tuple(d for d in (p.strip().lower() for p in parts) if d)
        key_name = environ.get("PROVISIONER_KEY_NAME", "key-file").strip()
        if not 1 <= len(key_name) <= 100:
            raise ConfigError("PROVISIONER_KEY_NAME must be 1 to 100 characters")
        grist_url, team_site, boot_key, team_role = _team(environ, bool(domains))
        return cls(
            grist_home_db=environ.get("GRIST_HOME_DB", "/persist/home.sqlite3"),
            state_db=environ.get("PROVISIONER_STATE_DB", "/data/provisioner.sqlite3"),
            host=environ.get("PROVISIONER_HOST", "0.0.0.0"),  # nosec B104
            port=port,
            admin_email=admin_email,
            default_email=default_email,
            support_email=environ.get("GRIST_SUPPORT_EMAIL", "support@getgrist.com").strip(),
            allowed_domains=domains,
            key_file=environ.get("PROVISIONER_KEY_FILE", "").strip(),
            key_name=key_name,
            grist_url=grist_url,
            team_site=team_site,
            boot_key=boot_key,
            team_role=team_role,
        )


def _team(environ: Mapping[str, str], has_domains: bool) -> tuple[str, str, str, str]:
    url = environ.get("PROVISIONER_GRIST_URL", "").strip()
    site = environ.get("PROVISIONER_GRIST_TEAM_SITE", "").strip()
    key_path = environ.get("PROVISIONER_GRIST_BOOT_KEY_FILE", "").strip()
    role = environ.get("PROVISIONER_GRIST_TEAM_ROLE", "editors").strip()
    if not (url or site or key_path):
        return "", "", "", "editors"
    if not (url and site and key_path):
        raise ConfigError(
            "Set PROVISIONER_GRIST_URL, PROVISIONER_GRIST_TEAM_SITE and PROVISIONER_GRIST_BOOT_KEY_FILE "
            "together, or none of them."
        )
    url = url.rstrip("/")
    parts = urlsplit(url)
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
        port_ok = True
    except ValueError:
        port_ok = False
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or not port_ok
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
        or "#" in url
        or "?" in url
    ):
        raise ConfigError(
            "PROVISIONER_GRIST_URL must be an http:// or https:// address with no user name, path, query or fragment"
        )
    if not _SITE.match(site):
        raise ConfigError("PROVISIONER_GRIST_TEAM_SITE must match [a-z0-9-]{1,100}")
    try:
        with open(key_path, encoding="utf-8") as handle:
            boot_key = handle.read().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"PROVISIONER_GRIST_BOOT_KEY_FILE cannot be read: {exc}") from exc
    if not boot_key:
        raise ConfigError("PROVISIONER_GRIST_BOOT_KEY_FILE is empty")
    if role not in _ROLES:
        raise ConfigError("PROVISIONER_GRIST_TEAM_ROLE must be editors, viewers or members")
    if not has_domains:
        raise ConfigError(
            "Set PROVISIONER_ALLOWED_DOMAINS when PROVISIONER_GRIST_URL is set: "
            "the provisioner then creates Grist accounts."
        )
    return url, site, boot_key, role
