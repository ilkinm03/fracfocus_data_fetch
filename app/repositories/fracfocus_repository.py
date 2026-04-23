import csv
import logging
from pathlib import Path
from typing import Any, Optional
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
