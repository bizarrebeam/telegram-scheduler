from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    timezone: str = "Asia/Jakarta"

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""

    database_url: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/oauth/google/callback"
    google_calendar_id: str = "primary"
    token_encryption_key: str = ""

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    @property
    def allowed_telegram_user_ids(self) -> frozenset[int]:
        raw = self.telegram_allowed_user_ids.replace(" ", "")
        return frozenset(int(x) for x in raw.split(",") if x)


@lru_cache
def get_settings() -> Settings:
    return Settings()
