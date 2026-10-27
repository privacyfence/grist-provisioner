import pytest

from grist_provisioner.config import EXPIRE_INTERVAL_SECONDS, KEY_DAYS, ConfigError, Settings


def test_defaults():
    s = Settings.from_env({"GRIST_ADMIN_EMAIL": "a@x.org"})
    assert s.grist_home_db == "/persist/home.sqlite3"
    assert s.state_db == "/data/provisioner.sqlite3"
    assert (s.host, s.port) == ("0.0.0.0", 8485)
    assert s.default_email == ""
    assert s.support_email == "support@getgrist.com"
    assert s.allowed_domains == ()
    assert (KEY_DAYS, EXPIRE_INTERVAL_SECONDS) == (90, 3600)


def test_every_variable_is_read():
    s = Settings.from_env(
        {
            "GRIST_HOME_DB": "/h.db",
            "PROVISIONER_STATE_DB": "/s.db",
            "PROVISIONER_HOST": "127.0.0.1",
            "PROVISIONER_PORT": "9000",
            "GRIST_ADMIN_EMAIL": "admin@x.org",
            "GRIST_DEFAULT_EMAIL": "def@x.org",
            "GRIST_SUPPORT_EMAIL": "help@x.org",
            "PROVISIONER_ALLOWED_DOMAINS": "x.org",
        }
    )
    assert (s.grist_home_db, s.state_db, s.host, s.port) == ("/h.db", "/s.db", "127.0.0.1", 9000)
    assert (s.admin_email, s.default_email, s.support_email) == ("admin@x.org", "def@x.org", "help@x.org")
    assert s.allowed_domains == ("x.org",)


def test_default_email_alone_is_enough():
    assert Settings.from_env({"GRIST_DEFAULT_EMAIL": "d@x.org"}).default_email == "d@x.org"


def test_domains_are_split_trimmed_and_lowercased():
    s = Settings.from_env({"GRIST_ADMIN_EMAIL": "a@x.org", "PROVISIONER_ALLOWED_DOMAINS": " Foo.org, BAR.com ,,"})
    assert s.allowed_domains == ("foo.org", "bar.com")


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1", ""])
def test_bad_port(port):
    with pytest.raises(ConfigError, match="PROVISIONER_PORT must be a number from 1 to 65535"):
        Settings.from_env({"GRIST_ADMIN_EMAIL": "a@x.org", "PROVISIONER_PORT": port})


def test_no_admin_email():
    with pytest.raises(ConfigError, match="Set GRIST_ADMIN_EMAIL or GRIST_DEFAULT_EMAIL"):
        Settings.from_env({})
