"""Config_Loader: reads settings and resolves them into a validated AppConfig.

- ``RawSettings`` reads raw strings with pydantic-settings. Precedence is
  process environment > ``.env`` > field default (Req 16.2).
- ``resolve_config`` is a pure function. It never raises: invalid values are
  replaced by their defaults and reported as ``ConfigIssue`` entries
  (Req 6.2, 15.6, 16.8, 16.9, 5.4). Issue messages contain the variable name
  and the allowed values/range only, never the input value.
- ``gemini_api_key`` is kept as ``SecretStr``. ``RedactingFilter`` replaces the
  key in log messages and exception text with ``***`` (Req 16.5).

An empty (or whitespace-only) value is treated the same as an unset variable
and gets the default without an issue, matching the rule for
``TEMPERATURE_TOLERANCE_C`` in Req 6.2. ``GEMINI_API_KEY`` has no default, so
an empty key means "not configured".
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Defaults (Req 16.2). Strings are what an unset variable resolves to.
DEFAULT_VISION_PROVIDER: Literal["fixture"] = "fixture"
DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"
DEFAULT_WEATHER_PROVIDER: Literal["fixture"] = "fixture"
DEFAULT_DATABASE_URL = "sqlite:///./data/weatherwear.db"
DEFAULT_PHOTO_DIR = "./data/demo/photos"
DEFAULT_APP_TIMEZONE = "Asia/Seoul"
DEFAULT_TEMPERATURE_TOLERANCE_C = Decimal("3.0")
DEFAULT_LATITUDE = 37.5665
DEFAULT_LONGITUDE = 126.9780
DEFAULT_CORS_ORIGINS = "http://localhost:5173"

VISION_PROVIDERS: tuple[str, ...] = ("gemini", "fixture")
WEATHER_PROVIDERS: tuple[str, ...] = ("open_meteo", "fixture")
TOLERANCE_MIN_EXCLUSIVE = Decimal("0")
TOLERANCE_MAX_EXCLUSIVE = Decimal("5.0")

REDACTED = "***"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RawSettings(BaseSettings):
    """Raw string settings. Process env > ``.env`` > defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Google Cloud Agent Platform express mode API key (not a Google AI Studio key;
    # AI Studio keys do not work). Only needed when VISION_PROVIDER=gemini. No
    # project ID or location variable is needed in express mode (Req 16.1, 16.10).
    GEMINI_API_KEY: str = ""
    VISION_PROVIDER: str = DEFAULT_VISION_PROVIDER
    GEMINI_MODEL: str = DEFAULT_GEMINI_MODEL
    WEATHER_PROVIDER: str = DEFAULT_WEATHER_PROVIDER
    DATABASE_URL: str = DEFAULT_DATABASE_URL
    PHOTO_DIR: str = DEFAULT_PHOTO_DIR
    APP_TIMEZONE: str = DEFAULT_APP_TIMEZONE
    TEMPERATURE_TOLERANCE_C: str = "3"
    DEFAULT_LATITUDE: str = "37.5665"
    DEFAULT_LONGITUDE: str = "126.9780"
    CORS_ORIGINS: str = DEFAULT_CORS_ORIGINS

    # Phase 2 automation (Req 18.1). Watching and the daily brief are off in
    # code; `.env.example` and compose.yaml turn them on (decision A1).
    GALLERY_WATCH_ENABLED: str = "false"
    GALLERY_DIR: str = "./ex_img"
    GALLERY_POLL_SECONDS: str = "2"
    USER_PHOTO_DIR: str = "./data/user/photos"
    DAILY_BRIEF_ENABLED: str = "false"
    DAILY_BRIEF_TIME: str = "07:30"
    NOTIFIER: str = "log"
    VAPID_KEY_DIR: str = "./data/secrets"
    VAPID_SUBJECT: str = "mailto:weatherwear@example.com"


# Phase 2 defaults (Req 18.1).
DEFAULT_GALLERY_DIR = Path("./ex_img")
DEFAULT_GALLERY_POLL_SECONDS = 2.0
GALLERY_POLL_SECONDS_MIN = 0.5
GALLERY_POLL_SECONDS_MAX = 60.0
DEFAULT_USER_PHOTO_DIR = Path("./data/user/photos")
DEFAULT_DAILY_BRIEF_TIME = time(7, 30)
NOTIFIERS: tuple[str, ...] = ("log", "webpush")
DEFAULT_NOTIFIER: Literal["log"] = "log"
DEFAULT_VAPID_KEY_DIR = Path("./data/secrets")
DEFAULT_VAPID_SUBJECT = "mailto:weatherwear@example.com"
VAPID_SUBJECT_PREFIXES: tuple[str, ...] = ("mailto:", "https://")

AUTOMATION_VARS: tuple[str, ...] = (
    "GALLERY_WATCH_ENABLED", "GALLERY_DIR", "GALLERY_POLL_SECONDS", "USER_PHOTO_DIR",
    "DAILY_BRIEF_ENABLED", "DAILY_BRIEF_TIME", "NOTIFIER", "VAPID_KEY_DIR", "VAPID_SUBJECT",
)


@dataclass(frozen=True)
class AutomationConfig:
    """Phase 2 settings. The defaults turn watching and the daily brief off (Req 18.4)."""

    gallery_watch_enabled: bool = False
    gallery_dir: Path = DEFAULT_GALLERY_DIR
    gallery_poll_seconds: float = DEFAULT_GALLERY_POLL_SECONDS  # 0.5 <= x <= 60
    user_photo_dir: Path = DEFAULT_USER_PHOTO_DIR
    daily_brief_enabled: bool = False
    daily_brief_time: time = DEFAULT_DAILY_BRIEF_TIME
    notifier: Literal["log", "webpush"] = DEFAULT_NOTIFIER
    vapid_key_dir: Path = DEFAULT_VAPID_KEY_DIR
    vapid_subject: str = DEFAULT_VAPID_SUBJECT

    @property
    def vision_overlay_path(self) -> Path:
        """``data/user/fixtures/vision.overlay.json`` next to User_Photo_Dir (design §8)."""
        return self.user_photo_dir.parent / "fixtures" / "vision.overlay.json"


@dataclass(frozen=True)
class AppConfig:
    vision_provider: Literal["gemini", "fixture"]
    gemini_model: str
    gemini_api_key: SecretStr  # repr/str never show the value
    weather_provider: Literal["open_meteo", "fixture"]
    database_url: str
    photo_dir: Path
    app_timezone: ZoneInfo
    temperature_tolerance_c: Decimal  # 0 < x < 5.0
    default_latitude: float
    default_longitude: float
    cors_origins: tuple[str, ...]
    # Phase 2 (Req 18.4): a default keeps every existing AppConfig(...) call valid.
    automation: AutomationConfig = field(default_factory=AutomationConfig)

    @property
    def gemini_api_key_configured(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value())


@dataclass(frozen=True)
class ConfigIssue:
    variable: str
    message: str  # variable name and allowed values/range only, never the input value


def parse_cors_origins(value: str) -> tuple[str, ...]:
    """Split on commas, strip each item, drop empty items, keep order."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _get(raw: Mapping[str, str], name: str) -> str | None:
    """Stripped value, or None when the variable is unset or blank."""
    value = raw.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _parse_finite_float(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_tolerance(value: str) -> Decimal | None:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if not (TOLERANCE_MIN_EXCLUSIVE < number < TOLERANCE_MAX_EXCLUSIVE):
        return None
    return number


def _parse_timezone(value: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(value)
    except Exception:  # ZoneInfoNotFoundError, ValueError, OSError for odd keys
        return None


def resolve_config(raw: Mapping[str, str]) -> tuple[AppConfig, list[ConfigIssue]]:
    """Validate raw settings; invalid values fall back to defaults and are reported."""
    issues: list[ConfigIssue] = []

    def issue(variable: str, message: str) -> None:
        issues.append(ConfigIssue(variable=variable, message=message))

    # GEMINI_API_KEY: kept as-is (no stripping of inner content, but blank = unset).
    api_key_raw = raw.get("GEMINI_API_KEY") or ""
    api_key = SecretStr(api_key_raw.strip())

    gemini_model = _get(raw, "GEMINI_MODEL")

    # VISION_PROVIDER (Req 16.8, 15.6)
    vision_value = _get(raw, "VISION_PROVIDER")
    vision_provider: Literal["gemini", "fixture"]
    if vision_value is None:
        vision_provider = DEFAULT_VISION_PROVIDER
    elif vision_value in VISION_PROVIDERS:
        vision_provider = vision_value  # type: ignore[assignment]
    else:
        issue(
            "VISION_PROVIDER",
            f"VISION_PROVIDER 값이 올바르지 않습니다. 허용값: {', '.join(VISION_PROVIDERS)}. "
            f"기본값 {DEFAULT_VISION_PROVIDER}을 사용합니다.",
        )
        vision_provider = DEFAULT_VISION_PROVIDER

    if vision_provider == "gemini":
        missing = []
        if not api_key.get_secret_value():
            missing.append("GEMINI_API_KEY")
        if gemini_model is None:
            missing.append("GEMINI_MODEL")
        for name in missing:
            issue(
                name,
                f"VISION_PROVIDER=gemini에는 {name}이(가) 필요하지만 설정되지 않았습니다. "
                "fixture Vision Provider로 대체합니다.",
            )
        if missing:
            vision_provider = "fixture"

    # WEATHER_PROVIDER (Req 16.8)
    weather_value = _get(raw, "WEATHER_PROVIDER")
    weather_provider: Literal["open_meteo", "fixture"]
    if weather_value is None:
        weather_provider = DEFAULT_WEATHER_PROVIDER
    elif weather_value in WEATHER_PROVIDERS:
        weather_provider = weather_value  # type: ignore[assignment]
    else:
        issue(
            "WEATHER_PROVIDER",
            f"WEATHER_PROVIDER 값이 올바르지 않습니다. 허용값: {', '.join(WEATHER_PROVIDERS)}. "
            f"기본값 {DEFAULT_WEATHER_PROVIDER}을 사용합니다.",
        )
        weather_provider = DEFAULT_WEATHER_PROVIDER

    # TEMPERATURE_TOLERANCE_C (Req 6.2, 16.9)
    tolerance_value = _get(raw, "TEMPERATURE_TOLERANCE_C")
    tolerance = DEFAULT_TEMPERATURE_TOLERANCE_C
    if tolerance_value is not None:
        parsed_tolerance = _parse_tolerance(tolerance_value)
        if parsed_tolerance is None:
            issue(
                "TEMPERATURE_TOLERANCE_C",
                "TEMPERATURE_TOLERANCE_C는 0 초과 5.0 미만의 숫자여야 합니다. 기본값 3.0을 사용합니다.",
            )
        else:
            tolerance = parsed_tolerance

    # DEFAULT_LATITUDE / DEFAULT_LONGITUDE (Req 16.9, 5.4)
    def coordinate(name: str, limit: float, default: float) -> float:
        value = _get(raw, name)
        if value is None:
            return default
        number = _parse_finite_float(value)
        if number is None or not (-limit <= number <= limit):
            issue(
                name,
                f"{name}는 -{limit:g} 이상 {limit:g} 이하의 숫자여야 합니다. 기본값 {default}을 사용합니다.",
            )
            return default
        return number

    latitude = coordinate("DEFAULT_LATITUDE", 90.0, DEFAULT_LATITUDE)
    longitude = coordinate("DEFAULT_LONGITUDE", 180.0, DEFAULT_LONGITUDE)

    # APP_TIMEZONE
    tz_value = _get(raw, "APP_TIMEZONE")
    timezone = ZoneInfo(DEFAULT_APP_TIMEZONE)
    if tz_value is not None:
        parsed_tz = _parse_timezone(tz_value)
        if parsed_tz is None:
            issue(
                "APP_TIMEZONE",
                f"APP_TIMEZONE은 유효한 IANA 타임존 이름이어야 합니다. 기본값 {DEFAULT_APP_TIMEZONE}을 사용합니다.",
            )
        else:
            timezone = parsed_tz

    # CORS_ORIGINS (Req 16.7)
    cors_value = _get(raw, "CORS_ORIGINS")
    cors_origins = parse_cors_origins(DEFAULT_CORS_ORIGINS)
    if cors_value is not None:
        parsed_origins = parse_cors_origins(cors_value)
        if parsed_origins:
            cors_origins = parsed_origins
        else:
            issue(
                "CORS_ORIGINS",
                f"CORS_ORIGINS는 쉼표로 구분한 출처 1개 이상이어야 합니다. 기본값 {DEFAULT_CORS_ORIGINS}을 사용합니다.",
            )

    photo_dir = Path(_get(raw, "PHOTO_DIR") or DEFAULT_PHOTO_DIR)
    automation, automation_issues = resolve_automation_config(raw, photo_dir=photo_dir)
    issues.extend(automation_issues)

    config = AppConfig(
        vision_provider=vision_provider,
        gemini_model=gemini_model or DEFAULT_GEMINI_MODEL,
        gemini_api_key=api_key,
        weather_provider=weather_provider,
        database_url=_get(raw, "DATABASE_URL") or DEFAULT_DATABASE_URL,
        photo_dir=photo_dir,
        app_timezone=timezone,
        temperature_tolerance_c=tolerance,
        default_latitude=latitude,
        default_longitude=longitude,
        cors_origins=cors_origins,
        automation=automation,
    )
    return config, issues


# --- Phase 2 automation settings (Req 18.1, 18.2, 4.2) ----------------------

_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_bool(value: str) -> bool | None:
    """``true/false/1/0/yes/no/on/off`` (case-insensitive, surrounding space ignored)."""
    word = str(value).strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return None


def parse_hhmm(value: str) -> time | None:
    """24-hour ``HH:MM`` with two digits each, e.g. ``07:30``."""
    match = _HHMM.fullmatch(str(value).strip())
    if match is None:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _is_same_or_nested(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` are the same path or one contains the other."""
    return a == b or a in b.parents or b in a.parents


def user_photo_dir_conflicts(user_photo_dir: Path, *others: Path) -> bool:
    """Req 4.2: USER_PHOTO_DIR must not equal, contain or sit inside PHOTO_DIR/GALLERY_DIR.

    Paths are compared lexically after normalization, so call it with paths of
    the same kind (all relative to one root, or all absolute).
    """
    mine = Path(os.path.normpath(user_photo_dir))
    return any(_is_same_or_nested(mine, Path(os.path.normpath(other))) for other in others)


def resolve_automation_config(
    raw: Mapping[str, str], *, photo_dir: Path
) -> tuple[AutomationConfig, list[ConfigIssue]]:
    """Validate the 9 Phase 2 variables; invalid values fall back to their defaults.

    Never raises. Every invalid variable yields one ``ConfigIssue`` whose
    message has the variable name and the allowed values only (Req 18.2, 18.9).
    The USER_PHOTO_DIR conflict check here compares the configured (usually
    relative) paths; ``main.resolve_paths`` repeats it on absolute paths.
    """
    issues: list[ConfigIssue] = []

    def issue(variable: str, message: str) -> None:
        issues.append(ConfigIssue(variable=variable, message=message))

    def boolean(name: str) -> bool:
        value = _get(raw, name)
        if value is None:
            return False
        parsed = parse_bool(value)
        if parsed is None:
            issue(
                name,
                f"{name}는 true, false, 1, 0, yes, no, on, off 중 하나여야 합니다. "
                "기본값 false를 사용합니다.",
            )
            return False
        return parsed

    def path(name: str, default: Path) -> Path:
        value = _get(raw, name)
        return Path(value) if value is not None else default

    gallery_watch_enabled = boolean("GALLERY_WATCH_ENABLED")
    daily_brief_enabled = boolean("DAILY_BRIEF_ENABLED")
    gallery_dir = path("GALLERY_DIR", DEFAULT_GALLERY_DIR)
    vapid_key_dir = path("VAPID_KEY_DIR", DEFAULT_VAPID_KEY_DIR)

    poll_seconds = DEFAULT_GALLERY_POLL_SECONDS
    poll_value = _get(raw, "GALLERY_POLL_SECONDS")
    if poll_value is not None:
        number = _parse_finite_float(poll_value)
        if number is None or not (GALLERY_POLL_SECONDS_MIN <= number <= GALLERY_POLL_SECONDS_MAX):
            issue(
                "GALLERY_POLL_SECONDS",
                "GALLERY_POLL_SECONDS는 0.5 이상 60 이하의 숫자여야 합니다. 기본값 2를 사용합니다.",
            )
        else:
            poll_seconds = number

    brief_time = DEFAULT_DAILY_BRIEF_TIME
    time_value = _get(raw, "DAILY_BRIEF_TIME")
    if time_value is not None:
        parsed_time = parse_hhmm(time_value)
        if parsed_time is None:
            issue(
                "DAILY_BRIEF_TIME",
                "DAILY_BRIEF_TIME은 24시간 HH:MM 형식이어야 합니다(예: 07:30). 기본값 07:30을 사용합니다.",
            )
        else:
            brief_time = parsed_time

    notifier: Literal["log", "webpush"] = DEFAULT_NOTIFIER
    notifier_value = _get(raw, "NOTIFIER")
    if notifier_value is not None:
        if notifier_value in NOTIFIERS:
            notifier = notifier_value  # type: ignore[assignment]
        else:
            issue(
                "NOTIFIER",
                f"NOTIFIER 값이 올바르지 않습니다. 허용값: {', '.join(NOTIFIERS)}. "
                f"기본값 {DEFAULT_NOTIFIER}을 사용합니다.",
            )

    vapid_subject = DEFAULT_VAPID_SUBJECT
    subject_value = _get(raw, "VAPID_SUBJECT")
    if subject_value is not None:
        if subject_value.startswith(VAPID_SUBJECT_PREFIXES):
            vapid_subject = subject_value
        else:
            issue(
                "VAPID_SUBJECT",
                "VAPID_SUBJECT는 mailto: 또는 https://로 시작해야 합니다. 기본값을 사용합니다.",
            )

    user_photo_dir = path("USER_PHOTO_DIR", DEFAULT_USER_PHOTO_DIR)
    if user_photo_dir_conflicts(user_photo_dir, photo_dir, gallery_dir):
        issue(
            "USER_PHOTO_DIR",
            "USER_PHOTO_DIR는 PHOTO_DIR, GALLERY_DIR와 같거나 서로 포함하면 안 됩니다. "
            "기본값 ./data/user/photos를 사용합니다.",
        )
        user_photo_dir = DEFAULT_USER_PHOTO_DIR

    config = AutomationConfig(
        gallery_watch_enabled=gallery_watch_enabled,
        gallery_dir=gallery_dir,
        gallery_poll_seconds=poll_seconds,
        user_photo_dir=user_photo_dir,
        daily_brief_enabled=daily_brief_enabled,
        daily_brief_time=brief_time,
        notifier=notifier,
        vapid_key_dir=vapid_key_dir,
        vapid_subject=vapid_subject,
    )
    return config, issues


def load_config(env_file: str | Path | None = ".env") -> tuple[AppConfig, list[ConfigIssue]]:
    """Read RawSettings (env > .env > defaults), resolve, and log every issue at ERROR."""
    raw = RawSettings(_env_file=env_file)  # type: ignore[call-arg]
    config, issues = resolve_config(raw.model_dump())
    for item in issues:
        logger.error("설정 오류 [%s]: %s", item.variable, item.message)
    return config, issues


class RedactingFilter(logging.Filter):
    """Replace secret strings with ``***`` in log messages and exception text.

    Attach it to handlers (handler filters see records from every logger).
    Empty secrets are ignored so that an unset key never redacts everything.
    """

    def __init__(self, secrets: Iterable[str | SecretStr] = ()) -> None:
        super().__init__()
        values = (s.get_secret_value() if isinstance(s, SecretStr) else s for s in secrets)
        # Longest first so a secret containing another secret is fully replaced.
        self._secrets = tuple(sorted({v for v in values if v}, key=len, reverse=True))

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # malformed args: fall back to the raw template
            message = str(record.msg)
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        return True


def configure_logging(config: AppConfig, level: int = logging.INFO) -> RedactingFilter:
    """Set up root logging with the standard format and the redacting filter on every handler."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
    redacting = RedactingFilter([config.gemini_api_key])
    for handler in logging.getLogger().handlers:
        handler.addFilter(redacting)
    return redacting
