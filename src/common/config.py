"""Central configuration — all env-var access goes through this module."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # Database
    postgres_user: str = "btcpipeline"
    postgres_password: str = "changeme"
    postgres_db: str = "btcpipeline"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # API keys
    news_api_key: str = ""
    coingecko_api_key: str = ""  # optional — free tier works without it

    # App
    log_level: str = "INFO"
    env: str = "production"
    model_dir: str = "/app/models"

    # Alerting
    alert_webhook_url: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_development(self) -> bool:
        return self.env.lower() == "development"


settings = Settings()
