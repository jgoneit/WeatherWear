"""Shared helpers for the flow B (daily brief and notifications) tests.

Not a test module. Everything here is offline: a settable clock, configs that
point at ``tmp_path``, synthetic Outfits stored through the real repository,
and a fake Push_Sender.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import AppConfig, AutomationConfig, resolve_config
from app.domain import ClothingTags, ThermalVote, WeatherReading
from app.repository import OutfitCreate, OutfitRepository


class FakeClock:
    """Callable clock. ``advance()`` moves it; ``set()`` jumps anywhere (even backwards)."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 11, 14, 0, 0, tzinfo=UTC)  # 09:00 KST

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now = self.now + timedelta(**delta)
        return self.now

    def set(self, now: datetime) -> datetime:
        self.now = now
        return now


def make_config(
    tmp_path: Path,
    *,
    weather_provider: str = "fixture",
    notifier: str = "log",
    daily_brief_enabled: bool = False,
    brief_time: str = "07:30",
    extra: dict[str, str] | None = None,
) -> AppConfig:
    """Fixture-mode AppConfig with every path under ``tmp_path`` and absolute."""
    (tmp_path / "photos").mkdir(parents=True, exist_ok=True)
    raw = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'brief.db'}",
        "PHOTO_DIR": str(tmp_path / "photos"),
        "WEATHER_PROVIDER": weather_provider,
        "GALLERY_DIR": str(tmp_path / "gallery"),
        "USER_PHOTO_DIR": str(tmp_path / "user" / "photos"),
        "VAPID_KEY_DIR": str(tmp_path / "secrets"),
        "NOTIFIER": notifier,
        "DAILY_BRIEF_ENABLED": "true" if daily_brief_enabled else "false",
        "DAILY_BRIEF_TIME": brief_time,
        **(extra or {}),
    }
    config, issues = resolve_config(raw)
    assert issues == [], issues
    assert isinstance(config.automation, AutomationConfig)
    return config


def clothing(
    upper: str = "knit",
    outer: str = "none",
    lower: str = "jeans",
    colors: list[str] | None = None,
) -> ClothingTags:
    return ClothingTags(
        upper_type=upper,  # type: ignore[arg-type]
        outer_type=outer,  # type: ignore[arg-type]
        lower_type=lower,  # type: ignore[arg-type]
        colors=["cream"] if colors is None else colors,
        layers_visible=1,
        confidence=0.9,
    )


def add_outfit(
    repo: OutfitRepository,
    temp_c: float,
    tags: ClothingTags | None = None,
    vote: ThermalVote | None = None,
    *,
    name: str | None = None,
    captured_at: datetime | None = None,
    humidity: float | None = 50.0,
) -> int:
    """Store one synthetic Outfit + snapshot (+ seed vote) through the real repository."""
    captured = captured_at or datetime(2025, 3, 2, 8, 0, tzinfo=UTC)
    file_name = name or f"synthetic_{temp_c}_{len(repo.list_outfits())}.png"
    sha = hashlib.sha256(file_name.encode()).hexdigest()
    outfit = OutfitCreate(
        sha256=sha,
        file_name=file_name,
        captured_at=captured,
        latitude=37.5665,
        longitude=126.978,
        tags=tags or clothing(),
        vision_provider="fixture",
    )
    weather = WeatherReading(
        observed_at=captured,
        actual_temperature_c=temp_c,
        apparent_temperature_c=None,
        relative_humidity_pct=humidity,
        wind_speed_kmh=None,
        precipitation_mm=None,
        weather_code=None,
        provider="fixture",
        is_fixture=True,
    )
    return repo.create_outfit_bundle(outfit, weather, vote)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def subscription_json(n: int = 1, host: str = "fcm.googleapis.com") -> dict[str, Any]:
    """A browser ``PushSubscription.toJSON()`` that passes the format rules (allowlisted host)."""
    p256dh = b"\x04" + hashlib.sha256(f"x{n}".encode()).digest() + hashlib.sha256(f"y{n}".encode()).digest()
    auth = hashlib.md5(f"auth{n}".encode()).digest()  # 16 bytes  # noqa: S324
    return {
        "endpoint": f"https://{host}/send/secret-token-{n}",
        "expirationTime": None,
        "keys": {"p256dh": b64url(p256dh), "auth": b64url(auth)},
    }


@dataclass
class FakePushSender:
    """Push_Sender stand-in: returns scripted results per endpoint, records calls."""

    results: dict[str, Any] = field(default_factory=dict)  # endpoint -> SendResult | Exception
    default: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def send(self, sub, payload: bytes, *, ttl: int, keys, subject: str):
        from app.notifications.webpush import SendResult

        self.calls.append(
            {"subscription_id": sub.id, "endpoint": sub.endpoint, "payload": payload,
             "ttl": ttl, "keys": keys, "subject": subject}
        )
        outcome = self.results.get(sub.endpoint, self.default)
        if outcome is None:
            outcome = SendResult(status_code=201, error_code=None)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
