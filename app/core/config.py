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

    # TexNet (Delaware Basin seismic catalog) — ArcGIS REST layer 0.
    TEXNET_REST_URL: str = (
        "https://maps.texnet.beg.utexas.edu/arcgis/rest/services/catalog/catalog_all/MapServer/0"
    )
    TEXNET_BBOX_MIN_LAT: float = 28.5
    TEXNET_BBOX_MAX_LAT: float = 32.5
    TEXNET_BBOX_MIN_LON: float = -105.5
    TEXNET_BBOX_MAX_LON: float = -102.5

    # USGS FDSN Event API — GeoJSON endpoint. Shares the TexNet bounding box.
    USGS_FDSN_URL: str = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    USGS_MIN_MAGNITUDE: float = 1.5
    # Default start date for historical coverage (pre-TexNet). ISO 8601 date string.
    USGS_START_TIME: str = "2000-01-01"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
