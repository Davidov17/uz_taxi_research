from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost:5432/market_research_bot"
    test_database_url: str = "postgresql+asyncpg://localhost:5432/market_research_bot_test"

    bot_token: str = ""
    screenshot_storage_dir: str = "./data/screenshots"
    log_level: str = "INFO"
    environment: str = "development"

    # Set both to run the bot in webhook mode from within the web process
    # (see web/app.py) instead of via main.py's long-polling — the shape
    # needed on a host that only runs one persistent process per free web
    # service (e.g. Render), rather than docker-compose's separate bot/web
    # containers. Leave both unset for the normal long-polling deployment;
    # nothing changes for docker-compose.
    public_base_url: str = ""
    webhook_secret: str = ""


settings = Settings()
