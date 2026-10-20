from datetime import datetime, time
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Time, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)

    zones: Mapped[list["Zone"]] = relationship(back_populates="camera", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # restricted, ppe, work
    polygon: Mapped[list[Any]] = mapped_column(JSON)  # [[x, y], ...] as fractions of frame width/height (0..1)
    max_people: Mapped[int | None] = mapped_column(Integer, nullable=True)  # crowding threshold N; None = no check

    camera: Mapped[Camera] = relationship(back_populates="zones")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))  # label only: admin, manager, head_guard, guard
    pin_hash: Mapped[str] = mapped_column(String(255))  # bcrypt; PIN is never returned
    camera_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # delegated cameras
    actions: Mapped[list[str]] = mapped_column(JSON, default=list)  # allowed actions
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Responder(Base):
    __tablename__ = "responders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(16), default="en")
    zone: Mapped[str] = mapped_column(String(120))
    shift_start: Mapped[time] = mapped_column(Time)
    shift_end: Mapped[time] = mapped_column(Time)
    call_order: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_started_at", "started_at"),
        Index("ix_incidents_type", "type"),
        Index("ix_incidents_camera_id", "camera_id"),
        Index("ix_incidents_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    zone: Mapped[str | None] = mapped_column(String(120), nullable=True)  # None: outside every zone
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confidence: Mapped[float] = mapped_column(Float)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clip_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    camera: Mapped[Camera] = relationship()
    timeline: Mapped[list["TimelineEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="TimelineEvent.id"
    )


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)  # contract field "to"
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)  # contract field "by"
    via: Mapped[str | None] = mapped_column(String(16), nullable=True)  # keypad, voice, dashboard

    incident: Mapped[Incident] = relationship(back_populates="timeline")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))  # dashboard, phone, voice
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Session(Base):
    """One row per logged-in browser. Only a SHA-256 hash of the token is stored, never the token."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
