from sqlalchemy import Column, Integer, Text, DateTime, Float
from app.core.database import Base


class IRISStation(Base):
    __tablename__ = "iris_stations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Composite natural key: "{network}.{station_code}" — one row per station, last fetch wins.
    network_station = Column(Text, unique=True, nullable=False, index=True)
    network = Column(Text, nullable=False, index=True)
    station_code = Column(Text, nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    elevation = Column(Float, nullable=True)   # metres above sea level
    site_name = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=True, index=True)
    end_time = Column(DateTime, nullable=True)  # null = currently operational
    fetched_at = Column(DateTime, nullable=True)
