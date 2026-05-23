from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADMIN_", env_file=None)

    bootstrap_username: str
    bootstrap_password_hash: str
    cookie_secret: str

    db_path: str = "/var/lib/admin/admin.db"
    home_dir: str = "/home"
    disabled_users_path: str = "/etc/ocserv/control/disabled-users"


def get_settings() -> Settings:
    return Settings()
