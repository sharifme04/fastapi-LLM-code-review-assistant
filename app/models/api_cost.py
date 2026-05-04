"""API Cost tracking SQLAlchemy model."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiCost(Base):
    """Daily aggregated API cost tracking."""

    __tablename__ = "api_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ApiCost(date={self.date}, total_cost=${self.total_cost:.4f}, requests={self.request_count})>"
