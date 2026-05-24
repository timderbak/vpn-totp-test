from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADMIN_", env_file=None)

    bootstrap_username: str
    bootstrap_password_hash: str
    cookie_secret: str

    db_path: str = "/var/lib/admin/admin.db"
    home_dir: str = "/home"
    disabled_users_path: str = "/etc/ocserv/control/disabled-users"

    ldap_url: str = "ldap://ldap:389"
    ldap_bind_dn: str
    ldap_bind_password: str
    ldap_base_dn: str = "dc=vpn,dc=local"
    ldap_user_ou: str = "ou=users,dc=vpn,dc=local"
    ldap_vpn_group_dn: str = "cn=vpn-users,ou=groups,dc=vpn,dc=local"
    ldap_cache_ttl: int = 30
    ldap_timeout: int = 5


def get_settings() -> Settings:
    return Settings()
