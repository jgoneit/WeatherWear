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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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

    config = AppConfig(
        vision_provider=vision_provider,
        gemini_model=gemini_model or DEFAULT_GEMINI_MODEL,
        gemini_api_key=api_key,
        weather_provider=weather_provider,
        database_url=_get(raw, "DATABASE_URL") or DEFAULT_DATABASE_URL,
        photo_dir=Path(_get(raw, "PHOTO_DIR") or DEFAULT_PHOTO_DIR),
        app_timezone=timezone,
        temperature_tolerance_c=tolerance,
        default_latitude=latitude,
        default_longitude=longitude,
        cors_origins=cors_origins,
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
