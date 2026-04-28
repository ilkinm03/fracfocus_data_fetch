from sqlalchemy import Column, Integer, Text, DateTime, Float
from app.core.database import Base


class SeismicEvent(Base):
    __tablename__ = "seismic_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Text, unique=True, nullable=False, index=True)
    magnitude = Column(Float, nullable=True)
    mag_type = Column(Text, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    depth = Column(Float, nullable=True)
    phase_count = Column(Integer, nullable=True)
    event_type = Column(Text, nullable=True, index=True)
    region_name = Column(Text, nullable=True)
    event_date = Column(DateTime, nullable=True, index=True)
    evaluation_status = Column(Text, nullable=True)
    county_name = Column(Text, nullable=True, index=True)
    rms = Column(Float, nullable=True)
    station_count = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, nullable=True)