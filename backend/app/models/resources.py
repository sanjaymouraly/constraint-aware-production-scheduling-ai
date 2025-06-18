"""Resource entities: machines, operators, calendars."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime


class Calendar(Base):
    """A working-time calendar.

    A calendar is the combination of recurring weekly shifts, recurring weekly
    breaks, and dated exceptions (holidays, shutdowns, extra shifts).  Shifts may
    cross midnight; the calendar engine materialises them into concrete segments.
    """

    __tablename__ = "calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    is_plant_calendar: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 24/7 availability, ignores shifts and breaks.
    is_continuous: Mapped[bool] = mapped_column(default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    shifts: Mapped[list[CalendarShift]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )
    breaks: Mapped[list[CalendarBreak]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[CalendarException]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("timezone != ''"),)


class CalendarShift(Base):
    """Recurring weekly shift pattern (minute-of-day based)."""

    __tablename__ = "calendar_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("calendars.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: ISO weekday: 1 = Monday ... 7 = Sunday.
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(48))
    is_overnight: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    calendar: Mapped[Calendar] = relationship(back_populates="shifts")

    __table_args__ = (
        UniqueConstraint("calendar_id", "day_of_week", "start_minute", name="uq_calendar_shift"),
        CheckConstraint("day_of_week >= 1 AND day_of_week <= 7"),
        CheckConstraint("start_minute >= 0 AND start_minute < 1440"),
        CheckConstraint("end_minute > 0 AND end_minute <= 2880"),
    )


class CalendarBreak(Base):
    """Recurring weekly break (e.g. lunch) that removes capacity from a shift."""

    __tablename__ = "calendar_breaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("calendars.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(48))

    calendar: Mapped[Calendar] = relationship(back_populates="breaks")

    __table_args__ = (
        CheckConstraint("day_of_week >= 1 AND day_of_week <= 7"),
        CheckConstraint("start_minute >= 0 AND start_minute < 1440"),
        CheckConstraint("end_minute > start_minute AND end_minute <= 1440"),
    )


class CalendarException(Base):
    """Dated exception: holiday / shutdown / extra working window."""

    __tablename__ = "calendar_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("calendars.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: ``holiday``/``shutdown`` remove working time; ``extra_shift`` adds a window.
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    start_minute: Mapped[int | None] = mapped_column(Integer)
    end_minute: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(80))

    calendar: Mapped[Calendar] = relationship(back_populates="exceptions")

    __table_args__ = (CheckConstraint("kind IN ('holiday','shutdown','extra_shift')"),)


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    #: Product families the machine is capable of processing.
    supported_families: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Extra capability tags (e.g. "5-axis", "cmm-certified").
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("calendars.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    hourly_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: Minutes required to prepare the machine before a *first* job of a family.
    initial_setup_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    throughput_per_hour: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    calendar: Mapped[Calendar] = relationship()
    setups: Mapped[list[MachineSetup]] = relationship(
        back_populates="machine", cascade="all, delete-orphan"
    )
    downtimes: Mapped[list[MachineDowntime]] = relationship(
        back_populates="machine", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[MachineCalendarException]] = relationship(
        back_populates="machine", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("hourly_cost >= 0"),)


class MachineSetup(Base):
    """Sequence-dependent setup time between two setup families on a machine."""

    __tablename__ = "machine_setups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_family: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    to_family: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    setup_minutes: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    setup_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="setups")

    __table_args__ = (
        UniqueConstraint("machine_id", "from_family", "to_family", name="uq_machine_setup"),
        CheckConstraint("setup_minutes >= 0"),
    )


class MachineDowntime(Base):
    """Maintenance window or breakdown — the machine is unavailable."""

    __tablename__ = "machine_downtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="maintenance", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240))
    #: Locked downtimes (e.g. an in-progress repair) are never moved by replanning.
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="downtimes")

    __table_args__ = (
        CheckConstraint("ends_at > starts_at"),
        CheckConstraint("kind IN ('maintenance','breakdown','planned_stop','overhaul')"),
    )


class MachineCalendarException(Base):
    """Machine-specific deviation from its base calendar."""

    __tablename__ = "machine_calendar_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    start_minute: Mapped[int | None] = mapped_column(Integer)
    end_minute: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(80))

    machine: Mapped[Machine] = relationship(back_populates="exceptions")

    __table_args__ = (
        CheckConstraint("kind IN ('holiday','shutdown','extra_shift','unavailable')"),
    )


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    certifications: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Machine types the operator is allowed to run.
    eligible_machine_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Optional explicit machine assignment (ids); empty means type-based.
    eligible_machine_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("calendars.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    weekly_overtime_limit_minutes: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    #: Contracted regular working minutes per week (used for the overtime metric).
    weekly_regular_minutes: Mapped[int] = mapped_column(Integer, default=2400, nullable=False)
    hourly_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    calendar: Mapped[Calendar] = relationship()
    absences: Mapped[list[OperatorAbsence]] = relationship(
        back_populates="operator", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("weekly_overtime_limit_minutes >= 0"),
        CheckConstraint("weekly_regular_minutes >= 0"),
    )


class OperatorAbsence(Base):
    __tablename__ = "operator_absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(160))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    operator: Mapped[Operator] = relationship(back_populates="absences")

    __table_args__ = (CheckConstraint("ends_at > starts_at"),)
