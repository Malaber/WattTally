from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATTTALLY_", env_file=".env", extra="ignore")
    secret_key: str
    app_base_url: str = "http://localhost:8000"
    webauthn_rp_id: str = "localhost"
    database_url: str = "sqlite+aiosqlite:///./data/watttally.db"
    secure_cookies: bool = False
    collector_enabled: bool = True
    poll_seconds: int = 60
    history_days: int = 9

    def validate_secrets(self):
        from urllib.parse import urlsplit

        origin = urlsplit(self.app_base_url)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.path not in {"", "/"}
        ):
            raise ValueError("APP_BASE_URL must be an http(s) origin.")
        if origin.hostname != self.webauthn_rp_id and not origin.hostname.endswith(
            "." + self.webauthn_rp_id
        ):
            raise ValueError(
                "WEBAUTHN_RP_ID must match the application hostname or a parent domain."
            )
        if origin.scheme == "http" and origin.hostname != "localhost":
            raise ValueError("Passkeys require HTTPS except on localhost.")
        if len(self.secret_key) < 32:
            raise ValueError("Use a secret key of at least 32 characters.")
