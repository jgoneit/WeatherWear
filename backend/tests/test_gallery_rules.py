"""Gallery candidate filter and stability tracking (tasks 3.1, Req 2.1–2.3, 2.8, 1.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.gallery.rules import (
    IGNORED_SUFFIXES,
    Signature,
    StableEvent,
    Tracker,
    advance_trackers,
    is_gallery_candidate,
)

T0 = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)
STABLE_AFTER = timedelta(seconds=2)


def regular(name: str) -> bool:
    return is_gallery_candidate(name, is_regular_file=True, is_symlink=False)


@pytest.mark.parametrize(
    "name",
    ["capture_20261114_070000.jpg", "a.JPG", "b.jpeg", "c.Jpeg", "d.png", "e.PNG", "f.webp", "g.WebP",
     "007_front.png", "2025-11-20_KST.jpg", "name with space.jpg", "x.y.png"],
)
def test_supported_images_are_candidates(name: str) -> None:
    assert regular(name)


@pytest.mark.parametrize(
    "name",
    [
        ".DS_Store", "._capture.jpg", ".capture_x.tmp", ".hidden.png",
        "photo.jpg~", "photo.png~",
        *(f"photo.jpg{suffix}" for suffix in IGNORED_SUFFIXES),
        *(f"photo{suffix.upper()}" for suffix in IGNORED_SUFFIXES),
        "photo.metadata.json", "PHOTO.METADATA.JSON", "clothing_tags.json",
        "notes.txt", "movie.mov", "image.heic", "image.gif", "noext", "jpg", ".jpg", "",
    ],
)
def test_ignored_names_never_read(name: str) -> None:
    # Only the name and entry type are inspected; nothing is opened (Req 2.2).
    assert not regular(name)


def test_symlinks_and_non_regular_entries_are_not_candidates() -> None:
    assert not is_gallery_candidate("a.jpg", is_regular_file=True, is_symlink=True)
    assert not is_gallery_candidate("a.jpg", is_regular_file=False, is_symlink=False)
    assert not is_gallery_candidate("subdir.png", is_regular_file=False, is_symlink=False)


def sig(size: int, mtime_ns: int = 1) -> Signature:
    return Signature(size=size, mtime_ns=mtime_ns)


def run(observations: list[dict[str, Signature]], step: timedelta = STABLE_AFTER) -> list[list[StableEvent]]:
    trackers: dict[str, Tracker] = {}
    out = []
    for index, observed in enumerate(observations):
        trackers, events = advance_trackers(trackers, observed, T0 + index * step, STABLE_AFTER)
        out.append(events)
    return out


def test_stable_after_two_equal_polls_and_interval() -> None:
    events = run([{"a.jpg": sig(10)}, {"a.jpg": sig(10)}, {"a.jpg": sig(10)}])
    assert events[0] == []
    assert [e.name for e in events[1]] == ["a.jpg"]
    assert events[1][0].first_seen == T0
    assert events[2] == []  # one event per signature


def test_two_polls_closer_than_interval_are_not_stable() -> None:
    trackers, events = advance_trackers({}, {"a.jpg": sig(10)}, T0, STABLE_AFTER)
    trackers, events = advance_trackers(trackers, {"a.jpg": sig(10)}, T0 + timedelta(seconds=1), STABLE_AFTER)
    assert events == []
    trackers, events = advance_trackers(trackers, {"a.jpg": sig(10)}, T0 + timedelta(seconds=2), STABLE_AFTER)
    assert [e.name for e in events] == ["a.jpg"]


def test_zero_byte_file_ignored() -> None:
    events = run([{"empty.jpg": sig(0)}] * 5)
    assert all(batch == [] for batch in events)


def test_signature_change_resets_stability() -> None:
    events = run(
        [
            {"a.jpg": sig(10, 1)},
            {"a.jpg": sig(20, 2)},  # still growing: restart
            {"a.jpg": sig(20, 2)},
            {"a.jpg": sig(30, 3)},  # rewritten after the event: a new version
            {"a.jpg": sig(30, 3)},
        ]
    )
    assert events[0] == [] and events[1] == []
    assert [(e.name, e.signature) for e in events[2]] == [("a.jpg", sig(20, 2))]
    assert events[2][0].first_seen == T0 + STABLE_AFTER
    assert events[3] == []
    assert [(e.name, e.signature) for e in events[4]] == [("a.jpg", sig(30, 3))]


def test_removed_file_drops_tracker_and_restarts_when_back() -> None:
    trackers, _ = advance_trackers({}, {"a.jpg": sig(10)}, T0, STABLE_AFTER)
    trackers, _ = advance_trackers(trackers, {}, T0 + STABLE_AFTER, STABLE_AFTER)
    assert trackers == {}
    trackers, events = advance_trackers(trackers, {"a.jpg": sig(10)}, T0 + 2 * STABLE_AFTER, STABLE_AFTER)
    assert events == [] and trackers["a.jpg"].seen_count == 1


def test_events_ordered_by_first_seen_then_name() -> None:
    trackers, _ = advance_trackers({}, {"b.jpg": sig(1), "c.jpg": sig(1)}, T0, STABLE_AFTER)
    t1 = T0 + timedelta(seconds=1)
    trackers, _ = advance_trackers(trackers, {"b.jpg": sig(1), "c.jpg": sig(1), "a.jpg": sig(1)}, t1, STABLE_AFTER)
    t2 = T0 + timedelta(seconds=10)
    _, events = advance_trackers(trackers, {"b.jpg": sig(1), "c.jpg": sig(1), "a.jpg": sig(1)}, t2, STABLE_AFTER)
    assert [e.name for e in events] == ["b.jpg", "c.jpg", "a.jpg"]


def test_input_trackers_not_mutated() -> None:
    trackers, _ = advance_trackers({}, {"a.jpg": sig(1)}, T0, STABLE_AFTER)
    snapshot = dict(trackers)
    advance_trackers(trackers, {"a.jpg": sig(1)}, T0 + STABLE_AFTER, STABLE_AFTER)
    assert trackers == snapshot
