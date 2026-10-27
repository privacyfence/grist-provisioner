"""Settings read from the environment."""

from collections.abc import Mapping
from dataclasses import dataclass

KEY_DAYS = 90
EXPIRE_INTERVAL_SECONDS = 3600


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
        return cls(
            grist_home_db=environ.get("GRIST_HOME_DB", "/persist/home.sqlite3"),
            state_db=environ.get("PROVISIONER_STATE_DB", "/data/provisioner.sqlite3"),
            host=environ.get("PROVISIONER_HOST", "0.0.0.0"),
            port=port,
            admin_email=admin_email,
            default_email=default_email,
            support_email=environ.get("GRIST_SUPPORT_EMAIL", "support@getgrist.com").strip(),
            allowed_domains=domains,
        )
