from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost:5432/market_research_bot"
    test_database_url: str = "postgresql+asyncpg://localhost:5432/market_research_bot_test"

    bot_token: str = ""
    screenshot_storage_dir: str = "./data/screenshots"
    log_level: str = "INFO"
    environment: str = "development"


settings = Settings()
