"""Phase 2 tables (design.md "Data Models", owned by the lead).

The tables are added to the same ``SQLModel.metadata`` as the Phase 1 tables,
so ``create_all()`` in ``app.db.init_db`` creates them with
``CREATE TABLE IF NOT EXISTS`` and existing Phase 1 tables are never changed
(Req 19.1, 19.5). ``app.db`` imports this module at the end so every engine
built by ``create_db_engine`` gets these tables.

Times are fixed-width UTC ISO 8601 TEXT (``app.db.to_utc_iso``), the same
convention as Phase 1. Dates (``brief_date``) are ``YYYY-MM-DD`` in
APP_TIMEZONE.

Who writes what (tasks.md):

- ``gallery_files``, ``app_state``, ``user_photos``, ``photo_confirmations``:
  flow A (``app/gallery/``).
- ``user_location``: the lead (``app/location.py``).
- ``push_subscriptions``, ``notifications``, ``notification_deliveries``,
  ``daily_brief_runs``: flow B (``app/notifications/``).
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

GALLERY_STATUSES: tuple[str, ...] = (
    "baseline",
    "pending",
    "processing",
    "imported",
    "duplicate",
    "skipped_non_outfit",
    "uncertain",
    "retry_wait",
    "failed",
)
CAPTURE_ORIGINS: tuple[str, ...] = ("exif", "estimated")
PHOTO_TYPES: tuple[str, ...] = ("outfit", "non_outfit", "uncertain")
NOTIFICATION_KINDS: tuple[str, ...] = ("daily_brief", "push_test")
WEATHER_PROVIDERS: tuple[str, ...] = ("open_meteo", "fixture", "scenario")
DELIVERY_CHANNELS: tuple[str, ...] = ("log", "webpush")
DELIVERY_STATUSES: tuple[str, ...] = ("sent", "failed", "gone")
BRIEF_TRIGGERS: tuple[str, ...] = ("scheduled", "manual")
BRIEF_STATUSES: tuple[str, ...] = ("running", "sent", "failed")

PHASE2_TABLES: tuple[str, ...] = (
    "gallery_files",
    "app_state",
    "user_photos",
    "photo_confirmations",
    "user_location",
    "push_subscriptions",
    "notifications",
    "notification_deliveries",
    "daily_brief_runs",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _in_or_null(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {_in(column, values)}"


# ---------------------------------------------------------------------------
# Flow A: gallery watching
# ---------------------------------------------------------------------------


class GalleryFile(SQLModel, table=True):
    """One observed version of a Gallery_Dir file and its processing state (Req 2, 3, 6)."""

    __tablename__ = "gallery_files"
    __table_args__ = (
        UniqueConstraint("gallery_key", "file_name", name="uq_gallery_files_key_name"),
        CheckConstraint(_in("status", GALLERY_STATUSES), name="ck_gallery_files_status"),
        CheckConstraint(
            _in_or_null("capture_origin", CAPTURE_ORIGINS), name="ck_gallery_files_capture_origin"
        ),
        CheckConstraint(_in_or_null("photo_type", PHOTO_TYPES), name="ck_gallery_files_photo_type"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_gallery_files_sha256_len"),
        CheckConstraint("attempts >= 0", name="ck_gallery_files_attempts"),
        CheckConstraint(
            "reason IS NULL OR length(reason) <= 200", name="ck_gallery_files_reason_len"
        ),
        Index("ix_gallery_files_key_status_next", "gallery_key", "status", "next_attempt_at"),
        Index("ix_gallery_files_sha256", "sha256"),
        Index("ix_gallery_files_updated_id", text("updated_at DESC"), text("id DESC")),
    )

    id: int | None = Field(default=None, primary_key=True)
    gallery_key: str  # sha1(str(Gallery_Dir))[:16]
    file_name: str  # name relative to Gallery_Dir, never an absolute path
    size_bytes: int
    mtime_ns: int
    status: str
    sha256: str | None = None
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    next_attempt_at: str | None = None  # UTC ISO
    lease_until: str | None = None  # UTC ISO
    last_error_stage: str | None = None
    last_error_code: str | None = None
    outfit_id: int | None = Field(default=None, foreign_key="outfits.id", ondelete="SET NULL")
    photo_type: str | None = None
    photo_type_confidence: float | None = None
    reason: str | None = None  # <= 200 characters
    capture_origin: str | None = None  # 'exif' | 'estimated'
    first_seen_at: str  # UTC ISO
    updated_at: str  # UTC ISO


class AppState(SQLModel, table=True):
    """Small key/value store, e.g. ``gallery.baseline:<gallery_key>`` (Req 2.4)."""

    __tablename__ = "app_state"

    key: str = Field(primary_key=True)
    value: str
    updated_at: str  # UTC ISO


class UserPhoto(SQLModel, table=True):
    """Where the byte-for-byte copy of a watched photo lives (Req 3.2, 3.5)."""

    __tablename__ = "user_photos"
    __table_args__ = (
        CheckConstraint("length(sha256) = 64", name="ck_user_photos_sha256_len"),
        CheckConstraint("capture_estimated IN (0, 1)", name="ck_user_photos_capture_estimated"),
    )

    sha256: str = Field(primary_key=True)
    file_name: str = Field(unique=True)  # name relative to User_Photo_Dir
    gallery_file_name: str
    capture_estimated: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
    created_at: str  # UTC ISO


class PhotoConfirmation(SQLModel, table=True):
    """Audit record of a user's "착장 맞음" confirmation (Req 6.13). INSERT only."""

    __tablename__ = "photo_confirmations"
    __table_args__ = (
        UniqueConstraint("gallery_file_id", name="uq_photo_confirmations_gallery_file"),
        CheckConstraint("length(sha256) = 64", name="ck_photo_confirmations_sha256_len"),
        CheckConstraint("source IN ('user_action')", name="ck_photo_confirmations_source"),
    )

    id: int | None = Field(default=None, primary_key=True)
    sha256: str
    gallery_file_id: int = Field(foreign_key="gallery_files.id")
    outfit_id: int | None = Field(default=None, foreign_key="outfits.id", ondelete="SET NULL")
    previous_photo_type: str  # 'uncertain'
    previous_confidence: float | None = None
    previous_reason: str | None = None
    source: str = "user_action"
    confirmed_at: str  # UTC ISO


# ---------------------------------------------------------------------------
# Lead: last browser location
# ---------------------------------------------------------------------------


class UserLocation(SQLModel, table=True):
    """The single Last_Location row (Req 9.1, 9.4): ``CHECK (id = 1)``."""

    __tablename__ = "user_location"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_user_location_single_row"),
        CheckConstraint("source IN ('browser')", name="ck_user_location_source"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_user_location_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_user_location_longitude"),
        CheckConstraint(
            "accuracy_m IS NULL OR accuracy_m >= 0", name="ck_user_location_accuracy"
        ),
    )

    id: int = Field(default=1, primary_key=True)
    latitude: float  # rounded to 2 decimal places
    longitude: float  # rounded to 2 decimal places
    accuracy_m: float | None = None
    source: str = "browser"
    received_at: str  # UTC ISO


# ---------------------------------------------------------------------------
# Flow B: notifications, Web Push and the daily brief
# ---------------------------------------------------------------------------


class PushSubscription(SQLModel, table=True):
    """A browser Push_Subscription (Req 15.5). Keys are never logged or returned."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (CheckConstraint("failure_count >= 0", name="ck_push_subscriptions_failures"),)

    id: int | None = Field(default=None, primary_key=True)
    endpoint: str = Field(unique=True)
    p256dh: str
    auth: str
    vapid_public_key: str
    created_at: str  # UTC ISO
    last_success_at: str | None = None
    last_failure_at: str | None = None
    failure_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})


class Notification(SQLModel, table=True):
    """Notification_Log entry (Req 14.1). ``created_at`` is UTC ISO with microseconds."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_in("kind", NOTIFICATION_KINDS), name="ck_notifications_kind"),
        CheckConstraint(
            _in_or_null("weather_provider", WEATHER_PROVIDERS),
            name="ck_notifications_weather_provider",
        ),
        CheckConstraint(
            "weather_is_fixture IS NULL OR weather_is_fixture IN (0, 1)",
            name="ck_notifications_weather_is_fixture",
        ),
        Index("ix_notifications_created_id", text("created_at DESC"), text("id DESC")),
        Index("ix_notifications_kind_created", "kind", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # 'daily_brief' | 'push_test'
    title: str
    body: str
    url: str
    outfit_id: int | None = Field(default=None, foreign_key="outfits.id", ondelete="SET NULL")
    brief_date: str | None = None  # YYYY-MM-DD (APP_TIMEZONE)
    weather_provider: str | None = None
    weather_is_fixture: bool | None = None
    created_at: str  # UTC ISO, microseconds


class NotificationDelivery(SQLModel, table=True):
    """Per-channel delivery result of one notification (Req 14.2, 15.7, 15.8)."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(_in("channel", DELIVERY_CHANNELS), name="ck_notification_deliveries_channel"),
        CheckConstraint(_in("status", DELIVERY_STATUSES), name="ck_notification_deliveries_status"),
        Index("ix_notification_deliveries_notification", "notification_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    notification_id: int = Field(foreign_key="notifications.id", ondelete="CASCADE")
    channel: str  # 'log' | 'webpush'
    subscription_id: int | None = Field(
        default=None, foreign_key="push_subscriptions.id", ondelete="SET NULL"
    )
    status: str  # 'sent' | 'failed' | 'gone'
    http_status: int | None = None
    error_code: str | None = None
    created_at: str  # UTC ISO


class DailyBriefRun(SQLModel, table=True):
    """One daily brief attempt (Req 12.3, 12.4, 13.2).

    ``ux_daily_brief_scheduled`` makes a scheduled brief unique per date;
    manual runs are not limited.
    """

    __tablename__ = "daily_brief_runs"
    __table_args__ = (
        CheckConstraint(_in("trigger", BRIEF_TRIGGERS), name="ck_daily_brief_runs_trigger"),
        CheckConstraint(_in("status", BRIEF_STATUSES), name="ck_daily_brief_runs_status"),
        CheckConstraint("attempts >= 0", name="ck_daily_brief_runs_attempts"),
        Index(
            "ux_daily_brief_scheduled",
            "brief_date",
            unique=True,
            sqlite_where=text("trigger = 'scheduled'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    brief_date: str  # YYYY-MM-DD (APP_TIMEZONE)
    trigger: str  # 'scheduled' | 'manual'
    status: str  # 'running' | 'sent' | 'failed'
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    notification_id: int | None = Field(
        default=None, foreign_key="notifications.id", ondelete="SET NULL"
    )
    error_code: str | None = None
    started_at: str  # UTC ISO
    finished_at: str | None = None  # UTC ISO
