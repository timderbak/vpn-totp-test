import os
import pytest
from app.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin1")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", "$2b$12$abcdefghijklmnopqrstuv")
    monkeypatch.setenv("ADMIN_COOKIE_SECRET", "0" * 64)
    monkeypatch.setenv("ADMIN_LDAP_BIND_DN", "cn=admin-readonly,dc=vpn,dc=local")
    monkeypatch.setenv("ADMIN_LDAP_BIND_PASSWORD", "bindpw")

    settings = Settings()

    assert settings.bootstrap_username == "admin1"
    assert settings.bootstrap_password_hash.startswith("$2b$")
    assert settings.cookie_secret == "0" * 64
    assert settings.db_path == "/var/lib/admin/admin.db"
    assert settings.home_dir == "/home"
    assert settings.disabled_users_path == "/etc/ocserv/control/disabled-users"


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("ADMIN_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ADMIN_COOKIE_SECRET", raising=False)
    with pytest.raises(Exception):
        Settings()
