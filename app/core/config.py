from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./fracfocus_data/fracfocus.db"
    ZIP_URL: str = "https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip"
    EXTRACT_DIR: str = "./fracfocus_data/extracted"

    SYNC_ENABLED: bool = True
    SYNC_CRON_DAY: int = 1
    SYNC_CRON_HOUR: int = 2

    REQUEST_TIMEOUT: int = 120
    DOWNLOAD_CHUNK_SIZE: int = 1_048_576

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
