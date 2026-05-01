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
    from app.models import swd  # noqa: F401
    from app.models import sync_history  # noqa: F401
    from app.models import event_context  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_seismic_columns()
    _ensure_iris_station_columns()
    _ensure_swd_columns()
    _ensure_sync_history_columns()
    _ensure_event_context_columns()


def _ensure_seismic_columns() -> None:
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


def _ensure_swd_columns() -> None:
    from sqlalchemy import text, inspect as sa_inspect
    import logging
    _log = logging.getLogger(__name__)

    for table_name, model_cls_path in [
        ("swd_wells", ("app.models.swd", "SWDWell")),
        ("swd_monthly_monitor", ("app.models.swd", "SWDMonthlyMonitor")),
        ("swd_fetch_checkpoint", ("app.models.swd", "SWDFetchCheckpoint")),
    ]:
        if not sa_inspect(engine).has_table(table_name):
            continue
        with engine.connect() as conn:
            existing = {
                row[1]
                for row in conn.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()
            }
        import importlib
        mod = importlib.import_module(model_cls_path[0])
        model_cls = getattr(mod, model_cls_path[1])
        missing = [
            col.key for col in model_cls.__table__.columns
            if col.key not in existing and col.key != "id"
        ]
        if not missing:
            continue
        with engine.begin() as conn:
            for col_key in missing:
                col = model_cls.__table__.columns[col_key]
                col_type = col.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col_key}" {col_type}'))
        _log.info(f"{table_name}: added {len(missing)} new column(s): {missing}")


def _ensure_event_context_columns() -> None:
    from sqlalchemy import text, inspect as sa_inspect
    import logging
    _log = logging.getLogger(__name__)

    if not sa_inspect(engine).has_table("event_context_snapshot"):
        return
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(text('PRAGMA table_info("event_context_snapshot")')).fetchall()
        }
    from app.models.event_context import EventContextSnapshot
    missing = [
        col.key for col in EventContextSnapshot.__table__.columns
        if col.key not in existing and col.key != "id"
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for col_key in missing:
            col = EventContextSnapshot.__table__.columns[col_key]
            col_type = col.type.compile(engine.dialect)
            conn.execute(text(f'ALTER TABLE "event_context_snapshot" ADD COLUMN "{col_key}" {col_type}'))
    _log.info(f"event_context_snapshot: added {len(missing)} new column(s): {missing}")


def _ensure_sync_history_columns() -> None:
    from sqlalchemy import text, inspect as sa_inspect
    import logging
    _log = logging.getLogger(__name__)

    if not sa_inspect(engine).has_table("sync_history"):
        return
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(text('PRAGMA table_info("sync_history")')).fetchall()
        }
    from app.models.sync_history import SyncHistory
    missing = [
        col.key for col in SyncHistory.__table__.columns
        if col.key not in existing and col.key != "id"
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for col_key in missing:
            col = SyncHistory.__table__.columns[col_key]
            col_type = col.type.compile(engine.dialect)
            conn.execute(text(f'ALTER TABLE "sync_history" ADD COLUMN "{col_key}" {col_type}'))
    _log.info(f"sync_history: added {len(missing)} new column(s): {missing}")
