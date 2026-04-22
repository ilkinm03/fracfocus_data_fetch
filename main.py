import requests
import zipfile
import sqlite3
import csv
import io
import logging
from pathlib import Path

# ── CONFIG ─────────────────────────────────────────────────────────────────────
ZIP_URL     = "https://www.fracfocusdata.org/digitaldownload/FracFocusCSV.zip"
WORK_DIR    = Path("./fracfocus_data")
EXTRACT_DIR = WORK_DIR / "extracted"
DB_PATH     = WORK_DIR / "fracfocus.db"
TABLE_NAME  = "fracfocus"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── STEP 1: DOWNLOAD ────────────────────────────────────────────────────────────
def download_zip(url: str) -> bytes:
    log.info(f"Downloading: {url}")
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    chunks = []

    for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
        chunks.append(chunk)
        downloaded += len(chunk)
        if total:
            pct = downloaded / total * 100
            print(f"\r  Progress: {pct:.1f}% ({downloaded // 1_000_000}MB / {total // 1_000_000}MB)", end="")

    print()
    log.info("Download complete.")
    return b"".join(chunks)


# ── STEP 2: EXTRACT ─────────────────────────────────────────────────────────────
def extract_zip(zip_bytes: bytes, extract_to: Path) -> list[Path]:
    extract_to.mkdir(parents=True, exist_ok=True)
    log.info(f"Extracting ZIP to: {extract_to}")

    csv_files = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            log.info(f"  Found: {name}")
            if name.endswith(".csv"):
                zf.extract(name, extract_to)
                csv_files.append(extract_to / name)

    log.info(f"Extracted {len(csv_files)} CSV file(s).")
    return csv_files


# ── STEP 3: INFER SCHEMA FROM CSV HEADER ────────────────────────────────────────
def infer_columns(csv_path: Path) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
    # Sanitize column names: lowercase, replace spaces/special chars with _
    return [h.strip().lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "") for h in headers]


# ── STEP 4: CREATE TABLE ────────────────────────────────────────────────────────
def create_table(conn: sqlite3.Connection, table: str, columns: list[str]):
    cols_sql = ", ".join(f'"{col}" TEXT' for col in columns)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'CREATE TABLE "{table}" ({cols_sql})')
    conn.commit()
    log.info(f"Table '{table}' created with {len(columns)} columns.")


# ── STEP 5: LOAD CSV INTO SQLITE ────────────────────────────────────────────────
def load_csv_to_db(conn: sqlite3.Connection, csv_path: Path, table: str, columns: list[str]):
    placeholders = ", ".join("?" for _ in columns)
    cols_sql     = ", ".join(f'"{c}"' for c in columns)
    insert_sql   = f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})'

    log.info(f"Loading CSV into DB: {csv_path.name}")
    batch = []
    batch_size = 5000
    total_rows = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        for row in reader:
            # Pad or trim row to match column count
            row = (row + [""] * len(columns))[: len(columns)]
            batch.append(row)

            if len(batch) >= batch_size:
                conn.executemany(insert_sql, batch)
                conn.commit()
                total_rows += len(batch)
                log.info(f"  Inserted {total_rows:,} rows...")
                batch = []

        if batch:
            conn.executemany(insert_sql, batch)
            conn.commit()
            total_rows += len(batch)

    log.info(f"Done. Total rows inserted: {total_rows:,}")
    return total_rows


# ── STEP 6: VERIFY ──────────────────────────────────────────────────────────────
def verify(conn: sqlite3.Connection, table: str):
    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    sample = conn.execute(f'SELECT * FROM "{table}" LIMIT 3').fetchall()
    log.info(f"Row count: {count:,}")
    log.info("Sample rows:")
    for row in sample:
        log.info(f"  {row}")


# ── MAIN ────────────────────────────────────────────────────────────────────────
def main():
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Download
    zip_bytes = download_zip(ZIP_URL)

    # 2. Extract
    csv_files = extract_zip(zip_bytes, EXTRACT_DIR)
    if not csv_files:
        raise RuntimeError("No CSV files found in the ZIP.")

    # If multiple CSVs, pick the first (or loop over all)
    csv_path = csv_files[0]
    log.info(f"Using CSV: {csv_path}")

    # 3. Connect to SQLite
    conn = sqlite3.connect(DB_PATH)

    # 4. Infer schema & create table
    columns = infer_columns(csv_path)
    create_table(conn, TABLE_NAME, columns)

    # 5. Load data
    load_csv_to_db(conn, csv_path, TABLE_NAME, columns)

    # 6. Verify
    verify(conn, TABLE_NAME)

    conn.close()
    log.info(f"Database saved at: {DB_PATH.resolve()}")


if __name__ == "__main__":
    main()