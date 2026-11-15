"""LocationService: the last browser location (design.md §11, Req 9, owned by the lead).

- Only one row is kept (``user_location`` with ``CHECK (id = 1)``): every valid
  report replaces the previous one, so no location history builds up (Req 9.4).
- Latitude and longitude are rounded to 2 decimal places (about 1 km) with
  ``Decimal`` ``ROUND_HALF_UP`` before they are stored (Req 9.1).
- Coordinates are never written to a log (Req 9.4). Log lines only say that
  a report was stored.
- ``brief_location()`` is what the daily brief uses: the last report regardless
  of its age, otherwise DEFAULT_LATITUDE/DEFAULT_LONGITUDE (Req 9.5).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.db import from_iso, to_utc_iso
from app.db_automation import UserLocation

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")

LocationSource = Literal["browser", "default"]


@dataclass(frozen=True)
class LatLon:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class StoredLocation:
    """The Last_Location row: rounded coordinates, optional accuracy, UTC receive time."""

    latitude: float
    longitude: float
    accuracy_m: float | None
    received_at: datetime  # aware, UTC


class InvalidLocation(ValueError):
    """A report that fails ``validate_location``. The API answers 422 ``INVALID_LOCATION``.

    The message names the field and the rule only, never the value.
    """


def _round(value: float) -> float:
    return float(Decimal(repr(float(value))).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def round_location(lat: float, lon: float) -> LatLon:
    """Round to 2 decimal places with ROUND_HALF_UP on the decimal value (Req 9.1).

    ``repr`` gives the shortest decimal that round-trips the float, so 37.565
    rounds to 37.57 as written, not by its binary approximation.
    """
    return LatLon(latitude=_round(lat), longitude=_round(lon))


def _finite_number(value: object) -> float | None:
    # bool is a subclass of int; JSON true/false must not count as 1/0 (Req 9.2).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_location(lat: object, lon: object, accuracy: object) -> LatLon | str:
    """Check one report (Req 9.1, 9.2).

    Returns the (unrounded) ``LatLon`` when valid, otherwise a short reason
    string without the input value. Booleans, strings, NaN/±inf, latitude
    outside −90..90, longitude outside −180..180 and a negative accuracy are
    rejected. ``accuracy`` may be ``None``.
    """
    latitude = _finite_number(lat)
    if latitude is None or not -90.0 <= latitude <= 90.0:
        return "latitude must be a finite number between -90 and 90"
    longitude = _finite_number(lon)
    if longitude is None or not -180.0 <= longitude <= 180.0:
        return "longitude must be a finite number between -180 and 180"
    if accuracy is not None:
        accuracy_m = _finite_number(accuracy)
        if accuracy_m is None or accuracy_m < 0:
            return "accuracy_m must be a finite number >= 0"
    return LatLon(latitude=latitude, longitude=longitude)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LocationService:
    def __init__(
        self,
        engine: Engine,
        default: LatLon,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._default = default
        self._clock = clock

    @property
    def default(self) -> LatLon:
        return self._default

    def report(self, lat: float, lon: float, accuracy_m: float | None) -> StoredLocation:
        """Validate, round and store one report, replacing the previous one (id = 1 UPSERT).

        Raises ``InvalidLocation`` and keeps the stored value when the report is invalid.
        """
        checked = validate_location(lat, lon, accuracy_m)
        if isinstance(checked, str):
            raise InvalidLocation(checked)
        rounded = round_location(checked.latitude, checked.longitude)
        accuracy = None if accuracy_m is None else float(accuracy_m)
        received_at = self._clock().astimezone(UTC)
        values = {
            "id": 1,
            "latitude": rounded.latitude,
            "longitude": rounded.longitude,
            "accuracy_m": accuracy,
            "source": "browser",
            "received_at": to_utc_iso(received_at),
        }
        statement = sqlite_insert(UserLocation).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["id"],
            set_={key: statement.excluded[key] for key in values if key != "id"},
        )
        with self._engine.begin() as conn:
            conn.execute(statement)
        logger.info("Last_Location을 갱신했습니다.")  # never the coordinates (Req 9.4)
        return StoredLocation(
            latitude=rounded.latitude,
            longitude=rounded.longitude,
            accuracy_m=accuracy,
            received_at=from_iso(values["received_at"]),
        )

    def last(self) -> StoredLocation | None:
        """The stored Last_Location, or ``None`` when no browser report was received."""
        with Session(self._engine) as session:
            row = session.get(UserLocation, 1)
            if row is None:
                return None
            return StoredLocation(
                latitude=row.latitude,
                longitude=row.longitude,
                accuracy_m=row.accuracy_m,
                received_at=from_iso(row.received_at),
            )

    def brief_location(self) -> tuple[LatLon, LocationSource]:
        """Where the daily brief looks up weather (Req 9.5): last report, else the default."""
        last = self.last()
        if last is None:
            return self._default, "default"
        return LatLon(latitude=last.latitude, longitude=last.longitude), "browser"
