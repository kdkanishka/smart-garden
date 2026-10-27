"""Persistent plant data. Forecasts are never stored here."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Plant(Base):
    __tablename__ = "plants"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90", name="ck_plants_lat_bounds"),
        CheckConstraint("lon BETWEEN -180 AND 180", name="ck_plants_lon_bounds"),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name) "
            "AND name ~ '[^[:space:]]'",
            name="ck_plants_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    lat: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.clock_timestamp(),
    )
