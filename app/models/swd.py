from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, Text, UniqueConstraint
from app.core.database import Base


class SWDFetchCheckpoint(Base):
    """Generic per-source fetch checkpoint. One row per source."""
    __tablename__ = "swd_fetch_checkpoint"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False, unique=True)  # "uic" | "h10"
    progress_value = Column(Integer, default=0)         # uic: page offset | h10: chunk_start index
    secondary_value = Column(Integer, default=0)        # h10 only: page offset within current chunk
    total_count = Column(Integer, default=0)
    inserted_so_far = Column(Integer, default=0)
    updated_so_far = Column(Integer, default=0)
    started_at = Column(DateTime)
    updated_at = Column(DateTime)

class SWDWell(Base):
    """UIC injection well inventory (RRC Texas). One row per well. Unique key: uic_number.
    Holds static well metadata: location (latitude/longitude), well type,
    max injection pressure limits, injection zone depths, and volume caps."""
    __tablename__ = "swd_wells"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uic_number = Column(Text, unique=True, index=True, nullable=False)
    oil_gas_code = Column(Text)
    district_code = Column(Text)
    lease_number = Column(Text)
    well_no_display = Column(Text)
    api_no = Column(Text, index=True)
    activated_flag = Column(Boolean)
    uic_type_injection = Column(Integer)
    permit_canceled_date = Column(DateTime)
    max_liq_inj_pressure = Column(Float)
    max_gas_inj_pressure = Column(Float)
    prod_casing_pkr_depth = Column(Float)
    top_inj_zone = Column(Float)
    bot_inj_zone = Column(Float)
    lease_name = Column(Text)
    operator_number = Column(Integer)
    field_number = Column(Integer)
    bbl_vol_inj = Column(Float)
    mcf_vol_inj = Column(Float)
    w14_date = Column(DateTime)
    w14_number = Column(Text)
    letter_date = Column(DateTime)
    latitude = Column(Float, index=True)
    longitude = Column(Float, index=True)
    fetched_at = Column(DateTime)

class SWDMonthlyMonitor(Base):
    """H-10 monthly injection monitoring (RRC Texas). One row per well per month.
    Unique key: (uic_no, report_date). Joined to SWDWell via uic_no = SWDWell.uic_number.
    Each row is one month's measurements for a single well: avg/max injection pressure,
    liquid volume (bbl), gas volume (mcf), top/bottom zone depth, and commercial flag."""
    __tablename__ = "swd_monthly_monitor"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uic_no = Column(Text, nullable=False, index=True)
    report_date = Column(DateTime, nullable=False, index=True)
    inj_press_avg = Column(Float)
    inj_press_max = Column(Float)
    vol_liq = Column(Float)
    vol_gas = Column(Float)
    toz = Column(Float)
    boz = Column(Float)
    commercial = Column(Integer)
    most_recent_record = Column(Boolean)
    type_uic = Column(Text)
    fetched_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("uic_no", "report_date", name="uq_swd_monitor_uic_date"),
    )
