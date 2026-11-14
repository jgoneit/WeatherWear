#!/usr/bin/env python3
"""Record Open-Meteo Archive weather for every demo photo into ``weather.json``.

Run once by a developer while preparing demo data (needs network). pytest and
app start-up never run this script (design.md "Weather_Recording_Script",
Req 4.13~4.15).

1. Each image in ``data/demo/photos`` is read with the backend's
   ``read_embedded_metadata``. When the image has no valid embedded metadata,
   its sidecar is tried as the fallback source (``resolve_capture_metadata``),
   exactly as the ingestion service does. Any photo without a capture
   time/location stops the script with exit code 1.
2. Photos are grouped by ``(local date in APP_TIMEZONE, round(lat, 4),
   round(lon, 4))``. Each group gets one Archive request built with the same
   ``build_archive_params`` as ``OpenMeteoWeatherProvider``. Requests are
   spaced by ``--pause`` seconds (0.2 by default), time out after 10 seconds,
   and transient failures (timeout, network error, HTTP 429/5xx) are retried
   once.
3. Each photo's reading comes from ``map_archive_response`` (nearest hour).
   A photo whose request failed or whose selected hour has no
   ``temperature_2m`` is reported and the script exits with 1. No value is
   made up (Req 4.14), and the existing ``weather.json`` is left untouched.
4. On full success the file is replaced atomically (temp file + rename) with
   a ``recorded`` provenance block, one entry per photo, and the existing
   ``today_default`` (``scripts/select_scenarios.py`` sets it later).

Usage: python scripts/record_fixture_weather.py [--photos DIR] [--output PATH]
       [--timezone NAME] [--pause SECONDS]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHOTO_DIR = REPO_ROOT / "data" / "demo" / "photos"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "demo" / "fixtures" / "weather.json"

sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.domain import CaptureMetadata, WeatherReading  # noqa: E402
from app.ingestion.image_check import list_image_files  # noqa: E402
from app.ingestion.metadata import read_embedded_metadata, resolve_capture_metadata  # noqa: E402
from app.ingestion.sidecar import read_sidecar  # noqa: E402
from app.providers.weather.normalize import map_archive_response  # noqa: E402
from app.providers.weather.open_meteo import (  # noqa: E402
    ARCHIVE_URL,
    HOURLY_VARIABLES,
    REQUEST_TIMEOUT_S,
    build_archive_params,
)

DEFAULT_PAUSE_S = 0.2
RETRIES = 1
RECORDED_SOURCE = "Open-Meteo Historical Weather API (reanalysis)"
RECORDED_NOTE = "사진 내장 메타데이터의 시각·위치로 1회 기록한 값. 앱은 provider=fixture로 표시한다"

GroupKey = tuple[str, float, float]


@dataclass(frozen=True)
class PhotoCapture:
    file: str
    captured_at: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PhotoFailure:
    file: str
    code: str


class FetchError(Exception):
    """An Archive request failed after the allowed retries."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# -- 1. capture metadata ----------------------------------------------------------


def read_capture(image: Path) -> PhotoCapture | PhotoFailure:
    """Capture time/location for one image: embedded EXIF first, sidecar as fallback."""
    try:
        data = image.read_bytes()
    except OSError:
        return PhotoFailure(image.name, "FILE_READ_ERROR")
    embedded = read_embedded_metadata(data)
    if isinstance(embedded, CaptureMetadata):
        capture = embedded  # the sidecar only adds a seed vote; not needed here
    else:
        resolved = resolve_capture_metadata(embedded, read_sidecar(image, require_location=True))
        if not isinstance(resolved, tuple):
            return PhotoFailure(image.name, resolved.code)
        capture = resolved[0]
    return PhotoCapture(image.name, capture.captured_at, capture.latitude, capture.longitude)


def read_captures(photo_dir: Path) -> tuple[list[PhotoCapture], list[PhotoFailure]]:
    captures: list[PhotoCapture] = []
    failures: list[PhotoFailure] = []
    for image in list_image_files(photo_dir):
        result = read_capture(image)
        (captures if isinstance(result, PhotoCapture) else failures).append(result)  # type: ignore[arg-type]
    return captures, failures


# -- 2. grouping ------------------------------------------------------------------


def group_key(capture: PhotoCapture, tz: ZoneInfo) -> GroupKey:
    return (
        capture.captured_at.astimezone(tz).date().isoformat(),
        round(capture.latitude, 4),
        round(capture.longitude, 4),
    )


def group_captures(captures: Iterable[PhotoCapture], tz: ZoneInfo) -> dict[GroupKey, list[PhotoCapture]]:
    """Photos grouped by local date and rounded location, in key order."""
    groups: dict[GroupKey, list[PhotoCapture]] = {}
    for capture in captures:
        groups.setdefault(group_key(capture, tz), []).append(capture)
    return {key: groups[key] for key in sorted(groups)}


def params_for_group(group: list[PhotoCapture], tz: ZoneInfo) -> dict[str, str | float]:
    """Same parameters as OpenMeteoWeatherProvider, with the group's rounded location."""
    first = group[0]
    return build_archive_params(first.captured_at, round(first.latitude, 4), round(first.longitude, 4), tz)


# -- 3. fetching and mapping ------------------------------------------------------


def _is_transient_status(status: int) -> bool:
    return status == 429 or status >= 500


def fetch_archive(
    client: httpx.Client,
    params: Mapping[str, str | float],
    *,
    retries: int = RETRIES,
    sleep: Callable[[float], None] = time.sleep,
    retry_wait_s: float = 1.0,
) -> dict[str, Any]:
    """GET the Archive endpoint. Retries transient failures ``retries`` times."""
    attempt = 0
    while True:
        try:
            response = client.get(ARCHIVE_URL, params=dict(params))
        except httpx.TimeoutException:
            code = "TIMEOUT"
        except httpx.HTTPError as exc:
            code = f"NETWORK_ERROR:{type(exc).__name__}"
        else:
            if response.is_success:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise FetchError("INVALID_JSON") from exc
                if not isinstance(payload, dict):
                    raise FetchError("INVALID_JSON")
                return payload
            code = f"HTTP_{response.status_code}"
            if not _is_transient_status(response.status_code):
                raise FetchError(code)
        if attempt >= retries:
            raise FetchError(code)
        attempt += 1
        sleep(retry_wait_s)


def _round1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def build_entry(capture: PhotoCapture, reading: WeatherReading, tz: ZoneInfo) -> dict[str, Any]:
    """One ``weather.json`` entry in the format FixtureWeatherProvider reads."""
    return {
        "file": capture.file,
        "captured_at": capture.captured_at.isoformat(),
        "latitude": capture.latitude,
        "longitude": capture.longitude,
        "observed_at": reading.observed_at.astimezone(tz).isoformat(),
        "actual_temperature_c": _round1(reading.actual_temperature_c),
        "apparent_temperature_c": None,
        "relative_humidity_pct": _round1(reading.relative_humidity_pct),
        "wind_speed_kmh": None,
        "precipitation_mm": _round1(reading.precipitation_mm),
        "weather_code": None,
    }


def entries_for_group(
    payload: Mapping[str, Any], group: list[PhotoCapture], tz: ZoneInfo
) -> tuple[list[dict[str, Any]], list[PhotoFailure]]:
    entries: list[dict[str, Any]] = []
    failures: list[PhotoFailure] = []
    for capture in group:
        try:
            reading = map_archive_response(payload, capture.captured_at, tz)
        except (ValueError, TypeError):
            reading = None
        if reading is None:
            failures.append(PhotoFailure(capture.file, "NO_TEMPERATURE"))
        else:
            entries.append(build_entry(capture, reading, tz))
    return entries, failures


def record(
    captures: list[PhotoCapture],
    client: httpx.Client,
    tz: ZoneInfo,
    *,
    pause_s: float = DEFAULT_PAUSE_S,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[PhotoFailure], int]:
    """Fetch one request per group. Returns (entries sorted by file, failures, request count)."""
    groups = group_captures(captures, tz)
    entries: list[dict[str, Any]] = []
    failures: list[PhotoFailure] = []
    for index, (key, group) in enumerate(groups.items()):
        if index > 0 and pause_s > 0:
            sleep(pause_s)
        try:
            payload = fetch_archive(client, params_for_group(group, tz), sleep=sleep)
        except FetchError as exc:
            failures.extend(PhotoFailure(c.file, exc.code) for c in group)
            continue
        group_entries, group_failures = entries_for_group(payload, group, tz)
        entries.extend(group_entries)
        failures.extend(group_failures)
        if progress is not None:
            progress(f"[{index + 1}/{len(groups)}] {key[0]} ({key[1]}, {key[2]}): {len(group)} photo(s)")
    entries.sort(key=lambda e: e["file"])
    failures.sort(key=lambda f: f.file)
    return entries, failures, len(groups)


# -- 4. output --------------------------------------------------------------------


def load_today_default(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("today_default") if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else None


def build_document(
    entries: list[dict[str, Any]], tz: ZoneInfo, recorded_on: date, today_default: dict[str, Any] | None
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "recorded": {
            "source": RECORDED_SOURCE,
            "endpoint": ARCHIVE_URL,
            "hourly": HOURLY_VARIABLES.split(","),
            "timezone": tz.key,
            "recorded_on": recorded_on.isoformat(),
            "note": RECORDED_NOTE,
        },
        "entries": entries,
    }
    if today_default is not None:
        document["today_default"] = today_default
    return document


def write_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def temperature_bands(temps: Iterable[float], width: float = 5.0) -> dict[str, int]:
    """Counts per ``width``-°C band, e.g. ``"-5.0~0.0": 3`` (lower bound inclusive)."""
    counts: dict[float, int] = {}
    for value in temps:
        low = (value // width) * width
        counts[low] = counts.get(low, 0) + 1
    return {f"{low:.1f}~{low + width:.1f}": counts[low] for low in sorted(counts)}


def main(
    argv: list[str] | None = None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTO_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timezone", default=os.environ.get("APP_TIMEZONE") or "Asia/Seoul")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S)
    args = parser.parse_args(argv)

    tz = ZoneInfo(args.timezone)
    if not args.photos.is_dir():
        print(f"error: photo folder not found: {args.photos}", file=sys.stderr)
        return 1

    captures, metadata_failures = read_captures(args.photos)
    if metadata_failures:
        for failure in metadata_failures:
            print(f"error: no capture time/location: {failure.file} ({failure.code})", file=sys.stderr)
        print(f"{len(metadata_failures)} photo(s) failed; {args.output} was not changed.", file=sys.stderr)
        return 1
    if not captures:
        print(f"error: no images in {args.photos}", file=sys.stderr)
        return 1

    own_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_S)
    try:
        entries, failures, requests = record(
            captures, http, tz, pause_s=args.pause, sleep=sleep, progress=print
        )
    finally:
        if own_client:
            http.close()

    if failures:
        for failure in failures:
            print(f"error: no recorded weather: {failure.file} ({failure.code})", file=sys.stderr)
        print(f"{len(failures)} photo(s) failed; {args.output} was not changed.", file=sys.stderr)
        return 1

    today_default = load_today_default(args.output)
    document = build_document(entries, tz, datetime.now(tz).date(), today_default)
    write_atomic(args.output, document)

    temps = [entry["actual_temperature_c"] for entry in entries]
    print(f"photos   : {len(entries)}")
    print(f"requests : {requests}")
    print(f"min/max  : {min(temps):.1f}°C / {max(temps):.1f}°C")
    for band, count in temperature_bands(temps).items():
        print(f"  {band}°C : {count}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
