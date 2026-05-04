"""Tool Call SQLAlchemy model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ToolCall(Base):
    """Record of a tool call made during a code review."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tool_input: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    review: Mapped["Review"] = relationship(
        "Review",
        back_populates="tool_calls",
    )

    def __repr__(self) -> str:
        return f"<ToolCall(id={self.id}, tool='{self.tool_name}', review_id={self.review_id})>"
