from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Engage AI Cloud API"
    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./engage_ai.db"
    jwt_secret: str = "change-this-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
