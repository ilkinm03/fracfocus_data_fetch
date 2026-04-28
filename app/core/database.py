from pathlib import Path
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import StaticPool
from app.core.config import get_settings

settings = get_settings()

# Ensure the directory exists before SQLite tries to create the file
_db_path = settings.DATABASE_URL.removeprefix("sqlite:///")
Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import fracfocus_sync_state  # noqa: F401 — registers ORM models with Base
    from app.models import seismic_event  # noqa: F401
    from app.models import iris_station  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_seismic_columns()
    _ensure_iris_station_columns()


def _ensure_seismic_columns() -> None:
    """
    Adds any columns present in the SeismicEvent model but absent from the
    existing seismic_events table. Required because create_all() only creates
    tables — it never alters an existing table to add new columns.
    """
    from sqlalchemy import text, inspect as sa_inspect
    if not sa_inspect(engine).has_table("seismic_events"):
        return
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(text('PRAGMA table_info("seismic_events")')).fetchall()
        }
    from app.models.seismic_event import SeismicEvent
    missing = [
        col.key for col in SeismicEvent.__table__.columns
        if col.key not in existing and col.key != "id"
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for col_key in missing:
            col = SeismicEvent.__table__.columns[col_key]
            col_type = col.type.compile(engine.dialect)
            conn.execute(text(f'ALTER TABLE "seismic_events" ADD COLUMN "{col_key}" {col_type}'))
    import logging
    logging.getLogger(__name__).info(
        f"seismic_events: added {len(missing)} new column(s): {missing}"
    )


def _ensure_iris_station_columns() -> None:
    """
    Adds any columns present in the IRISStation model but absent from the
    existing iris_stations table. Follows the same migration-free ALTER TABLE
    pattern used for seismic_events.
    """
    from sqlalchemy import text, inspect as sa_inspect
    if not sa_inspect(engine).has_table("iris_stations"):
        return
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(text('PRAGMA table_info("iris_stations")')).fetchall()
        }
    from app.models.iris_station import IRISStation
    missing = [
        col.key for col in IRISStation.__table__.columns
        if col.key not in existing and col.key != "id"
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for col_key in missing:
            col = IRISStation.__table__.columns[col_key]
            col_type = col.type.compile(engine.dialect)
            conn.execute(text(f'ALTER TABLE "iris_stations" ADD COLUMN "{col_key}" {col_type}'))
    import logging
    logging.getLogger(__name__).info(
        f"iris_stations: added {len(missing)} new column(s): {missing}"
    )
