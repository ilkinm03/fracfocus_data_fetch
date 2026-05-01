import logging
from datetime import datetime
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models.swd import SWDWell, SWDMonthlyMonitor, SWDFetchCheckpoint

log = logging.getLogger(__name__)

_MONITOR_BATCH = 5000

_WELL_COLS = [c.name for c in SWDWell.__table__.columns if c.name != "id"]
_MONITOR_COLS = [c.name for c in SWDMonthlyMonitor.__table__.columns if c.name != "id"]

# SQLite hard limit is 999 bound variables per statement.
# Divide by column count to get the max rows we can upsert in one shot.
_SQLITE_MAX_VARS = 999
_WELL_UPSERT_BATCH = max(1, _SQLITE_MAX_VARS // len(_WELL_COLS))       # 999//24 = 41
_MONITOR_UPSERT_BATCH = max(1, _SQLITE_MAX_VARS // len(_MONITOR_COLS))  # 999//11 = 90


class SWDRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ wells

    def upsert_wells(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        now = datetime.utcnow()
        params = [{**row, "fetched_at": now} for row in rows]

        existing_keys = {
            r[0]
            for r in self.db.query(SWDWell.uic_number)
            .filter(SWDWell.uic_number.in_([r["uic_number"] for r in rows]))
            .all()
        }

        # SQLite allows max 999 bound variables per statement.
        # Split into sub-batches so we never exceed that limit.
        for i in range(0, len(params), _WELL_UPSERT_BATCH):
            sub = params[i : i + _WELL_UPSERT_BATCH]
            stmt = sqlite_insert(SWDWell).values(sub)
            stmt = stmt.on_conflict_do_update(
                index_elements=["uic_number"],
                set_={c: stmt.excluded[c] for c in _WELL_COLS if c != "uic_number"},
            )
            self.db.execute(stmt)
        self.db.commit()

        inserted = sum(1 for r in rows if r["uic_number"] not in existing_keys)
        updated = len(rows) - inserted
        return inserted, updated

    def get_all_uic_numbers(self) -> list[str]:
        return [r[0] for r in self.db.query(SWDWell.uic_number).all()]

    def count_wells(self) -> int:
        return self.db.query(SWDWell).count()

    def get_wells_paginated(self, page: int, page_size: int) -> list[SWDWell]:
        return (
            self.db.query(SWDWell)
            .order_by(SWDWell.uic_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

    # ------------------------------------------------------------ monitoring

    def upsert_monitoring(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        now = datetime.utcnow()
        inserted = updated = 0

        for batch_start in range(0, len(rows), _MONITOR_BATCH):
            batch = rows[batch_start : batch_start + _MONITOR_BATCH]
            params = [{**row, "fetched_at": now} for row in batch]

            keys_uic = [r["uic_no"] for r in batch]
            keys_date = [r["report_date"] for r in batch]
            existing_keys = {
                (r[0], r[1])
                for r in self.db.query(
                    SWDMonthlyMonitor.uic_no, SWDMonthlyMonitor.report_date
                )
                .filter(
                    SWDMonthlyMonitor.uic_no.in_(keys_uic),
                    SWDMonthlyMonitor.report_date.in_(keys_date),
                )
                .all()
            }

            for j in range(0, len(params), _MONITOR_UPSERT_BATCH):
                sub = params[j : j + _MONITOR_UPSERT_BATCH]
                stmt = sqlite_insert(SWDMonthlyMonitor).values(sub)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["uic_no", "report_date"],
                    set_={c: stmt.excluded[c] for c in _MONITOR_COLS if c not in ("uic_no", "report_date")},
                )
                self.db.execute(stmt)
            self.db.commit()

            batch_inserted = sum(
                1 for r in batch
                if (r["uic_no"], r["report_date"]) not in existing_keys
            )
            batch_updated = len(batch) - batch_inserted
            inserted += batch_inserted
            updated += batch_updated

            log.info(
                f"H-10 upsert batch {batch_start}–{batch_start + len(batch)}: "
                f"inserted={inserted} updated={updated}"
            )

        return inserted, updated

    def count_monitoring(self, uic_no: Optional[str] = None) -> int:
        q = self.db.query(SWDMonthlyMonitor)
        if uic_no:
            q = q.filter(SWDMonthlyMonitor.uic_no == uic_no)
        return q.count()

    def get_monitoring_paginated(
        self,
        page: int,
        page_size: int,
        uic_no: Optional[str] = None,
    ) -> list[SWDMonthlyMonitor]:
        q = self.db.query(SWDMonthlyMonitor)
        if uic_no:
            q = q.filter(SWDMonthlyMonitor.uic_no == uic_no)
        return (
            q.order_by(SWDMonthlyMonitor.uic_no, SWDMonthlyMonitor.report_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

    # ---------------------------------------------------------- checkpoints

    def get_checkpoint(self, source: str) -> Optional[SWDFetchCheckpoint]:
        return (
            self.db.query(SWDFetchCheckpoint)
            .filter(SWDFetchCheckpoint.source == source)
            .first()
        )

    def save_checkpoint(
        self,
        source: str,
        progress_value: int,
        total_count: int,
        inserted_so_far: int,
        updated_so_far: int,
        secondary_value: int = 0,
    ) -> SWDFetchCheckpoint:
        now = datetime.utcnow()
        cp = self.get_checkpoint(source)
        if cp is None:
            cp = SWDFetchCheckpoint(
                source=source,
                progress_value=progress_value,
                secondary_value=secondary_value,
                total_count=total_count,
                inserted_so_far=inserted_so_far,
                updated_so_far=updated_so_far,
                started_at=now,
                updated_at=now,
            )
            self.db.add(cp)
        else:
            cp.progress_value = progress_value
            cp.secondary_value = secondary_value
            cp.total_count = total_count
            cp.inserted_so_far = inserted_so_far
            cp.updated_so_far = updated_so_far
            cp.updated_at = now
        self.db.commit()
        self.db.refresh(cp)
        return cp

    def clear_checkpoint(self, source: str) -> None:
        self.db.query(SWDFetchCheckpoint).filter(
            SWDFetchCheckpoint.source == source
        ).delete()
        self.db.commit()
