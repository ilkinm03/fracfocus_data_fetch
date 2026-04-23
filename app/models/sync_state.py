from datetime import datetime
from sqlalchemy import Column, Integer, Text, DateTime
from app.core.database import Base


class SyncState(Base):
    __tablename__ = "sync_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    zip_url = Column(Text, unique=True, nullable=False)
    etag = Column(Text, nullable=True)
    last_modified = Column(Text, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(Text, default="never")
    error_message = Column(Text, nullable=True)


class CsvFileState(Base):
    __tablename__ = "csv_file_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(Text, unique=True, nullable=False)
    file_size = Column(Integer, nullable=True)
    compress_size = Column(Integer, nullable=True)
    last_modified_zip = Column(Text, nullable=True)
    last_processed_at = Column(DateTime, nullable=True)
    row_count = Column(Integer, nullable=True)
