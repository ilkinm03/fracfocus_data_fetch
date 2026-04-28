from sqlalchemy import Column, Integer, Text, DateTime, Float
from app.core.database import Base


class SeismicEvent(Base):
    __tablename__ = "seismic_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Common across all sources
    source = Column(Text, nullable=True, index=True)            # "texnet" | "usgs"
    event_id = Column(Text, unique=True, nullable=False, index=True)
    magnitude = Column(Float, nullable=True)
    mag_type = Column(Text, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    depth = Column(Float, nullable=True)
    event_type = Column(Text, nullable=True, index=True)
    event_date = Column(DateTime, nullable=True, index=True)
    evaluation_status = Column(Text, nullable=True)
    rms = Column(Float, nullable=True)
    # TexNet-specific
    phase_count = Column(Integer, nullable=True)
    region_name = Column(Text, nullable=True)
    county_name = Column(Text, nullable=True, index=True)
    station_count = Column(Integer, nullable=True)
    # USGS-specific
    place = Column(Text, nullable=True)                         # human-readable location label
    title = Column(Text, nullable=True)                         # display-ready event label
    alternate_ids = Column(Text, nullable=True)                 # comma-sep IDs for cross-catalog joins
    gap = Column(Float, nullable=True)                          # azimuthal gap — location uncertainty proxy
    fetched_at = Column(DateTime, nullable=True)