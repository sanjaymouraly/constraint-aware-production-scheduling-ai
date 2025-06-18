"""AI provider configuration and conversation persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


class AIProvider(Base):
    """A configured LLM endpoint.

    API keys are stored encrypted-at-rest with AES-256-GCM using a
    domain-separated key derived from the application secret. They are never
    returned by the API — only a masked hint (e.g. ``sk-…f3a1``) is exposed so
    the UI can show that a key exists.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: openai | anthropic | openrouter | custom | ollama | mock
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(400))
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    request_timeout_seconds: Mapped[float] = mapped_column(Float, default=45.0, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    #: Optional per-provider cost hints (USD per 1k tokens) for budget estimates.
    input_cost_per_1k: Mapped[float | None] = mapped_column(Float)
    output_cost_per_1k: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("name", name="uq_ai_provider_name"),)


class AISettings(Base):
    """Singleton row holding global AI policy (singleton id = 1)."""

    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_paid_providers: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_local_providers: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    spend_limit_usd: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: Per-request hard cap so a single call cannot blow the budget.
    max_cost_per_request_usd: Mapped[float] = mapped_column(Float, default=0.25, nullable=False)
    default_model: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AIConversation(Base):
    """Persisted assistant conversation (grounded Q&A over a schedule)."""

    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    schedule_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    messages: Mapped[list[AIMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AIMessage.id"
    )


class AIMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(120))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    conversation: Mapped[AIConversation] = relationship(back_populates="messages")
