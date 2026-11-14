"""Phase 2 API request/response models (design.md §19, owned by the lead).

Shared contract for flows A, B and C: routers declare these as
``response_model`` and the frontend types come from them through
``frontend/src/api/schema.d.ts``. Rules carried over from Phase 1:

- Every datetime is ``AwareDatetime`` (Phase 1 Req 10.10).
- No model carries a server path, a VAPID private key, a push endpoint or its
  keys, raw latitude/longitude of the browser report beyond the rounded value,
  or GEMINI_API_KEY (Req 17.2, 18.7). File names are relative to their folder.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# --- Shared literals ----------------------------------------------------------

# Gallery_Status values returned by the API. ``baseline`` rows are never listed (Req 17.3).
GalleryImportStatus = Literal[
    "pending",
    "processing",
    "imported",
    "duplicate",
    "skipped_non_outfit",
    "uncertain",
    "retry_wait",
    "failed",
]
PhotoTypeOut = Literal["outfit", "non_outfit", "uncertain"]
CaptureOrigin = Literal["exif", "estimated"]
NotificationKind = Literal["daily_brief", "push_test"]
NotificationWeatherProvider = Literal["open_meteo", "fixture", "scenario"]
BriefTrigger = Literal["scheduled", "manual"]
BriefRunStatus = Literal["running", "sent", "failed"]
NotifierName = Literal["log", "webpush"]
LocationSource = Literal["browser", "default"]

# Query limits for list endpoints (Req 14.5, 17.3).
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

# --- Gallery imports (flow A; Req 5.5, 6.8, 6.9, 6.12, 17.3) --------------------


class GalleryImportOut(BaseModel):
    """One Gallery_File record. ``file_name`` is relative to Gallery_Dir."""

    id: int
    file_name: str
    status: GalleryImportStatus
    attempts: int
    last_error_stage: str | None
    last_error_code: str | None
    next_attempt_at: AwareDatetime | None
    outfit_id: int | None
    photo_type: PhotoTypeOut | None
    photo_type_confidence: float | None
    reason: str | None
    capture_origin: CaptureOrigin | None
    first_seen_at: AwareDatetime
    updated_at: AwareDatetime


# --- Location (lead; Req 9) ------------------------------------------------------

class LocationIn(BaseModel):
    """Body of ``POST /api/location``. Strict: booleans and strings are rejected (Req 9.2).

    A validation failure on this body becomes 422 ``INVALID_LOCATION`` in
    ``main.py``. ``LocationService`` validates again (``validate_location``).
    """

    model_config = ConfigDict(strict=True, extra="ignore")

    latitude: Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
    accuracy_m: Annotated[float, Field(ge=0.0, allow_inf_nan=False)] | None = None


class LocationOut(BaseModel):
    """The stored Last_Location: values rounded to 2 decimal places."""

    latitude: float
    longitude: float
    accuracy_m: float | None
    received_at: AwareDatetime
    place_label: str


class LocationStateOut(BaseModel):
    """``GET /api/location`` (Req 9.3)."""

    last: LocationOut | None
    default_latitude: float
    default_longitude: float
    brief_source: LocationSource
    brief_place_label: str


# --- Notifications and the daily brief (flow B; Req 13, 14) ------------------------


class DeliverySummaryOut(BaseModel):
    """Delivery results of one notification (Req 14.5)."""

    log_recorded: bool
    webpush_sent: int
    webpush_failed: int
    webpush_gone: int


class NotificationOut(BaseModel):
    id: int
    kind: NotificationKind
    title: str
    body: str
    url: str
    outfit_id: int | None
    photo_url: str | None
    weather_provider: NotificationWeatherProvider | None
    weather_is_fixture: bool | None
    created_at: AwareDatetime
    delivery: DeliverySummaryOut


class DailyBriefRunOut(BaseModel):
    """``POST /api/daily-brief/run`` (Req 13.1)."""

    notification: NotificationOut
    trigger: BriefTrigger


# --- Web Push (flow B; Req 15) -------------------------------------------------------


class PushKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    """Browser ``PushSubscription.toJSON()``.

    Shape only. The format rules of Req 15.5 (https endpoint <= 2,048 chars,
    base64url ``p256dh`` of 65 bytes, ``auth`` of 16 bytes) are checked by the
    push subscription store (tasks 4.9); a body that does not match this shape
    becomes 422 ``PUSH_SUBSCRIPTION_INVALID`` in ``main.py``.
    """

    endpoint: str
    expirationTime: int | None = None  # noqa: N815  (browser field name)
    keys: PushKeysIn


class PushSubscriptionCreatedOut(BaseModel):
    id: int


class VapidPublicKeyOut(BaseModel):
    """base64url public key only (Req 15.4)."""

    public_key: str


# --- Automation status (lead; Req 17.1, 17.2, 1.5) -----------------------------------


class GalleryStatusOut(BaseModel):
    enabled: bool
    running: bool
    dir_available: bool
    last_poll_at: AwareDatetime | None
    pending: int
    uncertain: int


class LastScheduledBriefOut(BaseModel):
    date: str  # YYYY-MM-DD in APP_TIMEZONE
    status: BriefRunStatus


class DailyBriefStatusOut(BaseModel):
    enabled: bool
    time: str  # "HH:MM"
    timezone: str  # IANA name
    next_run_at: AwareDatetime | None
    last_scheduled: LastScheduledBriefOut | None


class NotifierStatusOut(BaseModel):
    name: NotifierName
    vapid_ready: bool
    subscription_count: int


class LocationStatusOut(BaseModel):
    has_last: bool
    received_at: AwareDatetime | None
    place_label: str | None


class AutomationStatusOut(BaseModel):
    gallery: GalleryStatusOut
    daily_brief: DailyBriefStatusOut
    notifier: NotifierStatusOut
    location: LocationStatusOut
