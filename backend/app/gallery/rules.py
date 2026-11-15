"""Gallery candidate filter and write-stability tracking (design.md §3, Req 2.1–2.3, 2.8, 2.9, 1.6).

Pure functions, no file I/O:

- ``is_gallery_candidate`` decides from a directory entry's name and type
  whether it can be a Gallery_File. The watcher calls it with values from
  ``os.scandir`` (``is_file(follow_symlinks=False)``, ``is_symlink()``), so an
  ignored name is never opened or read (Req 2.2).
- ``advance_trackers`` applies one observation (name → File_Signature) to the
  in-memory trackers and returns the Stable_File events of this poll.

A Stable_File has a signature larger than 0 bytes that was seen unchanged in at
least two consecutive polls, and at least ``stable_after`` (GALLERY_POLL_SECONDS)
has passed since that signature was first seen. Each signature emits one event;
a changed signature starts over as a new version (Req 2.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.ingestion.image_check import SUPPORTED_EXTENSIONS
from app.ingestion.sidecar import SIDECAR_SUFFIX

# Files that are still being written by a browser, a copy tool or the simulator.
IGNORED_SUFFIXES: tuple[str, ...] = (".tmp", ".part", ".crdownload", ".download", ".partial")


def is_gallery_candidate(name: str, *, is_regular_file: bool, is_symlink: bool) -> bool:
    """True for a top-level image file the watcher may process (Req 2.1, 1.6).

    Rejected: symlinks and anything that is not a regular file, hidden names
    (``.DS_Store``, ``._x.jpg``, ``.capture_x.tmp``), names ending in ``~``,
    temporary-download suffixes, sidecars (``*.metadata.json``) and every
    extension outside Phase 1 ``SUPPORTED_EXTENSIONS`` (``clothing_tags.json``
    included). Extension checks ignore case.
    """
    if is_symlink or not is_regular_file:
        return False
    if not name or name.startswith(".") or name.endswith("~"):
        return False
    lower = name.lower()
    if lower.endswith(IGNORED_SUFFIXES) or lower.endswith(SIDECAR_SUFFIX):
        return False
    dot = lower.rfind(".")
    if dot <= 0:
        return False
    return lower[dot:] in SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class Signature:
    """File_Signature: (size in bytes, mtime in nanoseconds)."""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Tracker:
    signature: Signature
    first_seen: datetime  # when this signature was first observed (aware UTC)
    seen_count: int  # consecutive polls with this signature
    emitted: bool  # a StableEvent was already emitted for this signature


@dataclass(frozen=True)
class StableEvent:
    name: str
    signature: Signature
    first_seen: datetime


def advance_trackers(
    trackers: Mapping[str, Tracker],
    observed: Mapping[str, Signature],
    now: datetime,
    stable_after: timedelta,
) -> tuple[dict[str, Tracker], list[StableEvent]]:
    """Apply one poll's observation and return (new trackers, Stable events).

    - A name missing from ``observed`` loses its tracker.
    - A new name or a changed signature restarts: ``seen_count=1``,
      ``first_seen=now``, ``emitted=False``.
    - An unchanged signature increments ``seen_count``.
    - An event is emitted once per signature when ``size > 0``,
      ``seen_count >= 2`` and ``now - first_seen >= stable_after``.

    Events are ordered by ``(first_seen, name)`` (Req 2.8). The input mapping
    is not modified.
    """
    updated: dict[str, Tracker] = {}
    events: list[StableEvent] = []
    for name, signature in observed.items():
        previous = trackers.get(name)
        if previous is None or previous.signature != signature:
            tracker = Tracker(signature=signature, first_seen=now, seen_count=1, emitted=False)
        else:
            tracker = replace(previous, seen_count=previous.seen_count + 1)
        if (
            not tracker.emitted
            and signature.size > 0
            and tracker.seen_count >= 2
            and now - tracker.first_seen >= stable_after
        ):
            tracker = replace(tracker, emitted=True)
            events.append(StableEvent(name=name, signature=signature, first_seen=tracker.first_seen))
        updated[name] = tracker
    events.sort(key=lambda event: (event.first_seen, event.name))
    return updated, events
