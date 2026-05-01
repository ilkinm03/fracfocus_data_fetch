import csv
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Union
from sqlalchemy import text, inspect as sa_inspect
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

TABLE_NAME = "fracfocus"


class FracFocusRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def table_exists(self) -> bool:
        return sa_inspect(self.engine).has_table(TABLE_NAME)

    def create_table_if_not_exists(self, columns: list[str]) -> None:
        if self.table_exists():
            return
        cols_sql = ", ".join(
            ['"id" INTEGER PRIMARY KEY AUTOINCREMENT', '"source_file" TEXT']
            + [f'"{c}" TEXT' for c in columns]
        )
        with self.engine.begin() as conn:
            conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" ({cols_sql})'))
        log.info(f"Table '{TABLE_NAME}' created with {len(columns)} columns.")

    def ensure_columns(self, columns: list[str]) -> None:
        """Adds any columns missing from the table via ALTER TABLE ADD COLUMN."""
        with self.engine.connect() as conn:
            existing = {
                row[1]
                for row in conn.execute(text(f'PRAGMA table_info("{TABLE_NAME}")')).fetchall()
            }
        missing = [c for c in columns if c not in existing]
        if not missing:
            return
        with self.engine.begin() as conn:
            for col in missing:
                conn.execute(text(f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "{col}" TEXT'))
        log.info(f"Added {len(missing)} new column(s) to '{TABLE_NAME}': {missing}")

    def get_table_columns(self) -> list[str]:
        """Returns all column names in the fracfocus table."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(f'PRAGMA table_info("{TABLE_NAME}")')).fetchall()
        return [row[1] for row in rows]

    def get_distinct_values(self, column: str) -> list[str]:
        """Returns sorted distinct non-empty values for the given column."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f'SELECT DISTINCT "{column}" FROM "{TABLE_NAME}"'
                    f' WHERE "{column}" IS NOT NULL AND "{column}" != ""'
                    f' ORDER BY "{column}"'
                )
            ).fetchall()
        return [row[0] for row in rows]

    def get_grouped_counts(self, column: str) -> list[dict]:
        """Returns value + row count for each distinct value, sorted by count desc."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f'SELECT "{column}", COUNT(*) as count FROM "{TABLE_NAME}"'
                    f' WHERE "{column}" IS NOT NULL AND "{column}" != ""'
                    f' GROUP BY "{column}"'
                    f' ORDER BY count DESC'
                )
            ).fetchall()
        return [{"value": row[0], "count": row[1]} for row in rows]

    def count(self) -> int:
        if not self.table_exists():
            return 0
        with self.engine.connect() as conn:
            return conn.execute(text(f'SELECT COUNT(*) FROM "{TABLE_NAME}"')).scalar() or 0

    def get_paginated(
        self,
        page: int,
        page_size: int,
        state: Optional[str] = None,
        operator: Optional[str] = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        if not self.table_exists():
            return 0, []

        where_parts: list[str] = []
        params: dict[str, Any] = {}

        if state:
            where_parts.append("state_name = :state")
            params["state"] = state
        if operator:
            where_parts.append("operator_name LIKE :operator")
            params["operator"] = f"%{operator}%"

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        with self.engine.connect() as conn:
            total: int = (
                conn.execute(
                    text(f'SELECT COUNT(*) FROM "{TABLE_NAME}" {where_sql}'), params
                ).scalar()
                or 0
            )
            params["limit"] = page_size
            params["offset"] = (page - 1) * page_size
            rows = conn.execute(
                text(
                    f'SELECT * FROM "{TABLE_NAME}" {where_sql}'
                    " LIMIT :limit OFFSET :offset"
                ),
                params,
            ).mappings().all()

        return total, [dict(row) for row in rows]

    def find_nearby(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        start_date: Union[date, datetime],
        end_date: Union[date, datetime],
    ) -> list[dict[str, Any]]:
        """Returns frac jobs whose lat/lon fall within the bounding box and whose
        job_start_date falls within [start_date, end_date]. Column names used here
        are the normalised form produced by CsvIngestionService.infer_columns().
        Returns empty list if the table doesn't exist or required columns are absent."""
        if not self.table_exists():
            return []

        cols = set(self.get_table_columns())
        lat_col = "latitude"
        lon_col = "longitude"
        start_col = "jobstartdate"
        if lat_col not in cols or lon_col not in cols or start_col not in cols:
            return []

        # FracFocus bulk CSV is flattened: one row per ingredient per job.
        # Deduplicate on (apinumber, jobstartdate) using GROUP BY so the service
        # receives one dict per job, not one per chemical disclosure line.
        # Column names are from CsvIngestionService.infer_columns() normalisation:
        #   TotalBaseWaterVolume → totalbasewatervolume  (NOT totalwatervolume)
        #   TVD                  → tvd                   (NOT formationdepth)
        select_cols = [lat_col, lon_col, start_col]
        optional = [
            "apinumber", "jobenddate", "operatorname", "wellname",
            "totalbasewatervolume", "tvd",
        ]
        for c in optional:
            if c in cols:
                select_cols.append(c)

        col_sql = ", ".join(f'"{c}"' for c in select_cols)

        # Date filtering is done in Python after the spatial fetch because FracFocus
        # stores dates in US locale format ("M/D/YYYY H:MM:SS AM/PM") which does not
        # sort correctly as a plain string — ISO comparison would silently exclude or
        # include wrong rows.
        sql = text(
            f'SELECT {col_sql} FROM "{TABLE_NAME}"'
            f' WHERE "{lat_col}" IS NOT NULL AND "{lon_col}" IS NOT NULL'
            f' AND "{lat_col}" != \'\' AND "{lon_col}" != \'\''
            f' AND CAST("{lat_col}" AS REAL) >= :min_lat'
            f' AND CAST("{lat_col}" AS REAL) <= :max_lat'
            f' AND CAST("{lon_col}" AS REAL) >= :min_lon'
            f' AND CAST("{lon_col}" AS REAL) <= :max_lon'
            f' GROUP BY {col_sql}'
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "min_lat": min_lat,
                    "max_lat": max_lat,
                    "min_lon": min_lon,
                    "max_lon": max_lon,
                },
            ).mappings().all()

        # Parse dates and apply window filter in Python.
        start_dt = start_date if isinstance(start_date, datetime) else datetime.combine(start_date, datetime.min.time())
        end_dt = end_date if isinstance(end_date, datetime) else datetime.combine(end_date, datetime.max.time())

        _DATE_FMTS = [
            "%m/%d/%Y %I:%M:%S %p",  # 3/25/2011 12:00:00 AM
            "%m/%d/%Y %H:%M:%S",     # 3/25/2011 00:00:00
            "%Y-%m-%dT%H:%M:%S",     # ISO fallback
            "%Y-%m-%d",
        ]

        def _parse(raw: Any) -> Optional[datetime]:
            if not raw:
                return None
            s = str(raw).strip()
            for fmt in _DATE_FMTS:
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        result = []
        seen: set[tuple] = set()
        for r in rows:
            d = _parse(r.get(start_col))
            if d is None or not (start_dt <= d <= end_dt):
                continue
            key = (r.get("apinumber", ""), r.get(start_col, ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(r))
        return result

    def replace_csv_data(
        self, csv_path: Path, columns: list[str], batch_size: int = 5000
    ) -> int:
        """
        Atomically replaces all rows for this CSV file in a single transaction.
        Deletes existing rows for source_file, then bulk-inserts the new data.
        """
        source_file = csv_path.name
        all_cols = ["source_file"] + columns
        cols_sql = ", ".join(f'"{c}"' for c in all_cols)
        placeholders = ", ".join(f":{c}" for c in all_cols)
        insert_sql = f'INSERT INTO "{TABLE_NAME}" ({cols_sql}) VALUES ({placeholders})'

        total_rows = 0
        batch: list[dict[str, str]] = []

        with self.engine.begin() as conn:
            conn.execute(
                text(f'DELETE FROM "{TABLE_NAME}" WHERE source_file = :sf'),
                {"sf": source_file},
            )

            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                next(reader)  # skip header

                for row in reader:
                    padded = (row + [""] * len(columns))[: len(columns)]
                    record: dict[str, str] = {"source_file": source_file}
                    record.update(dict(zip(columns, padded)))
                    batch.append(record)

                    if len(batch) >= batch_size:
                        conn.execute(text(insert_sql), batch)
                        total_rows += len(batch)
                        log.info(f"  Inserted {total_rows:,} rows from {source_file}...")
                        batch = []

                if batch:
                    conn.execute(text(insert_sql), batch)
                    total_rows += len(batch)

        log.info(f"replace_csv_data done: {total_rows:,} rows from {source_file}")
        return total_rows
