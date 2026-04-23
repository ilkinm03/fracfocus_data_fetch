import csv
import logging
from pathlib import Path
from app.repositories.fracfocus_repository import FracFocusRepository
from app.repositories.sync_state_repository import CsvFileStateRepository

log = logging.getLogger(__name__)


class CsvIngestionService:
    def __init__(
        self,
        fracfocus_repo: FracFocusRepository,
        csv_file_state_repo: CsvFileStateRepository,
    ) -> None:
        self.fracfocus_repo = fracfocus_repo
        self.csv_file_state_repo = csv_file_state_repo

    def infer_columns(self, csv_path: Path) -> list[str]:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
        return [
            h.strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("(", "")
            .replace(")", "")
            for h in headers
        ]

    def process_csv(self, csv_path: Path) -> int:
        """
        Ensures the table exists, then atomically replaces rows for this CSV file.
        Returns the number of rows inserted.
        """
        columns = self.infer_columns(csv_path)
        self.fracfocus_repo.create_table_if_not_exists(columns)
        self.fracfocus_repo.ensure_columns(columns)
        row_count = self.fracfocus_repo.replace_csv_data(csv_path, columns)
        log.info(f"Processed {csv_path.name}: {row_count:,} rows")
        return row_count
