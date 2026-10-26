from datetime import datetime, time
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Time, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"), nullable=True,
                                                index=True)  # which room or area it watches
    number: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)  # "camera 6" by voice
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # False: worker not started
    added_by: Mapped[str | None] = mapped_column(String(120), nullable=True)  # None: from cameras.yaml

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

    user: Mapped[User | None] = relationship()  # gives the role and cameras used by the call ladder


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


class Invitation(Base):
    """An emailed/QR invitation. The user row is created inactive; accepting sets the PIN and activates it."""

    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256; the token is never stored
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Session(Base):
    """One row per logged-in browser. Only a SHA-256 hash of the token is stored, never the token."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class RollCall(Base):
    """One "alert everybody": every on-shift guard is messaged and called and answers 1 (responding) or 2 (safe)."""

    __tablename__ = "roll_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True,
                                                    index=True)
    message: Mapped[str] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_by: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(16))  # dashboard, phone, voice


class Call(Base):
    """One phone call we asked Twilio to place (alert ladder, roll call, broadcast or a direct call)."""

    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_sid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # Twilio's id, once placed
    kind: Mapped[str] = mapped_column(String(16))  # alert, roll_call, broadcast, direct
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True,
                                                    index=True)
    roll_call_id: Mapped[int | None] = mapped_column(ForeignKey("roll_calls.id", ondelete="CASCADE"), nullable=True,
                                                     index=True)
    responder_id: Mapped[int | None] = mapped_column(ForeignKey("responders.id", ondelete="SET NULL"), nullable=True)
    to_name: Mapped[str] = mapped_column(String(120))
    to_phone: Mapped[str] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)  # what <Say> reads (roll call, direct)
    audio_file: Mapped[str | None] = mapped_column(String(255), nullable=True)  # what <Play> plays (broadcast)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued, ringing, in-progress, completed,
    # no-answer, busy, failed, canceled, simulated, skipped
    response: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ack, false_alarm, responding, safe
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    responder: Mapped[Responder | None] = relationship()


class Site(Base):
    """A factory, campus or office. Buildings belong to a site."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("site_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))


class Area(Base):
    """A room or an open area inside a building. Cameras and badge readers are placed in areas."""

    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("building_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="room")  # room or area


class GuardProfile(Base):
    """A guard's post and equipment, plus personal details that are encrypted at rest.

    The link to the login is user_id. `personal` is a Fernet token of JSON with badge_number, badge_id,
    security_number and emergency_contact. `badge_hash` is an HMAC of the badge number, so a badge scan can
    find the guard without decrypting every row. Saving replaces the whole record; only field names are logged.
    """

    __tablename__ = "guard_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[str | None] = mapped_column(String(120), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    post: Mapped[str | None] = mapped_column(String(120), nullable=True)  # the area or gate he is posted at
    equipment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    has_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    personal: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    badge_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str] = mapped_column(String(120), default="")


class BadgeScan(Base):
    """A guard's badge read at a door or reader. A restricted entry with no scan in its area is 'no badge'."""

    __tablename__ = "badge_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
                                                index=True)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"), nullable=True,
                                                index=True)
    reader: Mapped[str] = mapped_column(String(120), default="")  # which reader or laptop did the scan


class Role(Base):
    """A role template: the cameras and actions everyone with this role label gets.

    Users keep their own lists (permissions stay data on the user). Changing a role's cameras or actions
    adds and removes the difference on every user with that role, and leaves each user's extras alone.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    camera_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    actions: Mapped[list[str]] = mapped_column(JSON, default=list)


class DutyEvent(Base):
    """A guard going on or off duty: a QR pass scan, a post QR scanned by the guard's phone, or the dashboard.

    The latest event per guard decides: "in" (recent) = on duty and called; "out" or nothing = off duty.
    """

    __tablename__ = "duty_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(8))  # in, out
    method: Mapped[str] = mapped_column(String(16))  # pass_scan, post_qr, dashboard, voice
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"), nullable=True)
    by_name: Mapped[str] = mapped_column(String(120))  # who recorded it (the guard, or the person scanning)


class Conversation(Base):
    """One talk with Friday (P31): a phone call she made, or a laptop session (wake word, Space, typed).

    Kept apart from the audit log and from Friday's page: the Conversations page shows each one as a chat,
    with the recorded audio. A laptop session groups the orders one user gives within a few minutes.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(8))  # phone, laptop
    person: Mapped[str] = mapped_column(String(120))  # who Friday talked with
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), nullable=True,
                                                unique=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    recording_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # whole call audio (phone)
    # phone only: answered, declined, no_answer, failed, cancelled (from Twilio's call status); seconds talked
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="conversation", order_by="ConversationTurn.id", cascade="all, delete-orphan")


class ConversationTurn(Base):
    """One thing said: by Friday or by the person (text, plus their voice clip for laptop orders)."""

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    speaker: Mapped[str] = mapped_column(String(8))  # friday, person
    text: Mapped[str] = mapped_column(String(1000))
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)  # what Friday did after it, if anything
    audio_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="turns")
