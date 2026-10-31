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


BASE = {"GRIST_ADMIN_EMAIL": "a@x.org"}


def team_env(tmp_path, **over):
    key = tmp_path / "boot"
    key.write_text(" bootsecret \n")
    env = {
        **BASE,
        "PROVISIONER_GRIST_URL": "http://grist:8484/",
        "PROVISIONER_GRIST_TEAM_SITE": "team",
        "PROVISIONER_GRIST_BOOT_KEY_FILE": str(key),
        "PROVISIONER_ALLOWED_DOMAINS": "x.org",
    }
    env.update(over)
    return env


def test_new_defaults(tmp_path):
    s = Settings.from_env(BASE)
    assert (s.key_file, s.key_name, s.grist_url, s.team_site, s.boot_key, s.team_role) == (
        "",
        "key-file",
        "",
        "",
        "",
        "editors",
    )
    t = Settings.from_env(team_env(tmp_path, PROVISIONER_GRIST_TEAM_ROLE="viewers", PROVISIONER_KEY_FILE=" /k "))
    assert (t.grist_url, t.team_site, t.boot_key, t.team_role, t.key_file) == (
        "http://grist:8484",
        "team",
        "bootsecret",
        "viewers",
        "/k",
    )


def test_boot_key_not_in_repr(tmp_path):
    assert "bootsecret" not in repr(Settings.from_env(team_env(tmp_path)))


@pytest.mark.parametrize(
    "drop", ["PROVISIONER_GRIST_URL", "PROVISIONER_GRIST_TEAM_SITE", "PROVISIONER_GRIST_BOOT_KEY_FILE"]
)
def test_grist_trio_together(tmp_path, drop):
    env = team_env(tmp_path)
    del env[drop]
    with pytest.raises(ConfigError, match="together, or none of them"):
        Settings.from_env(env)


@pytest.mark.parametrize(
    "url",
    [
        "http://g:1/path",
        "http://g:1?q=1",
        "http://g:1#frag",
        "http://u:p@g:1",
        "http://u@g:1",
        "ftp://g:1",
        "http://g:99999",
        "http://",
    ],
)
def test_grist_url_rejected(tmp_path, url):
    with pytest.raises(ConfigError, match="no user name, path, query or fragment"):
        Settings.from_env(team_env(tmp_path, PROVISIONER_GRIST_URL=url))


@pytest.mark.parametrize("site", ["Team", "a_b", "x" * 101, "a.b"])
def test_team_site_rejected(tmp_path, site):
    with pytest.raises(ConfigError, match=r"TEAM_SITE must match \[a-z0-9-\]\{1,100\}"):
        Settings.from_env(team_env(tmp_path, PROVISIONER_GRIST_TEAM_SITE=site))


def test_team_role_rejected(tmp_path):
    with pytest.raises(ConfigError, match="must be editors, viewers or members"):
        Settings.from_env(team_env(tmp_path, PROVISIONER_GRIST_TEAM_ROLE="owners"))


def test_boot_key_file_unreadable(tmp_path):
    with pytest.raises(ConfigError, match="PROVISIONER_GRIST_BOOT_KEY_FILE cannot be read: "):
        Settings.from_env(team_env(tmp_path, PROVISIONER_GRIST_BOOT_KEY_FILE=str(tmp_path / "missing")))


def test_boot_key_file_empty(tmp_path):
    env = team_env(tmp_path)
    (tmp_path / "boot").write_text("\n")
    with pytest.raises(ConfigError, match="PROVISIONER_GRIST_BOOT_KEY_FILE is empty"):
        Settings.from_env(env)


def test_team_requires_allowed_domains(tmp_path):
    with pytest.raises(ConfigError, match="Set PROVISIONER_ALLOWED_DOMAINS when PROVISIONER_GRIST_URL is set"):
        Settings.from_env(team_env(tmp_path, PROVISIONER_ALLOWED_DOMAINS=""))


@pytest.mark.parametrize("name", ["", "  ", "x" * 101])
def test_key_name_length(name):
    with pytest.raises(ConfigError, match="PROVISIONER_KEY_NAME must be 1 to 100 characters"):
        Settings.from_env({**BASE, "PROVISIONER_KEY_NAME": name})
    assert Settings.from_env({**BASE, "PROVISIONER_KEY_NAME": " pf "}).key_name == "pf"
