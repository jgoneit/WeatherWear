"""GalleryWatcher: polls Gallery_Dir and feeds Stable_Files to the importer (design.md §7).

``poll_once()`` per check (Req 1.1, 1.5, 1.6, 1.7, 1.8, 2.4, 2.8):

1. ``os.scandir`` of the top level only; candidates and their signatures come
   from ``lstat`` data, so symlinks and sub-folders are never followed and no
   file content is read. A missing/unreadable folder logs one WARNING when the
   availability changes, never on every check.
2. The first check of a Gallery_Dir records every candidate as ``baseline``
   and stops there.
3. ``advance_trackers`` → ``upsert_version`` → ``due_rows``; each due row is
   claimed (conditional UPDATE) and processed one at a time in
   ``(first_seen, name)`` order. ``stop`` is checked before every row, so a
   shutdown waits for at most the file in progress (Req 1.2).
4. An unexpected exception is recorded as a retryable ``STORAGE_ERROR``; the
   loop keeps going.

Trackers live in memory. After a restart every file is observed again, and
``upsert_version`` ignores versions that were already handled (Req 2.5).
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.gallery.processor import GalleryImportService, failure_transition
from app.gallery.rules import Signature, Tracker, advance_trackers, is_gallery_candidate
from app.gallery.store import GalleryStore, ImportOutcome, gallery_key

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class WatcherStatus:
    enabled: bool
    running: bool
    dir_available: bool
    last_poll_at: datetime | None
    pending: int  # pending + processing + retry_wait
    uncertain: int


@dataclass
class PollResult:
    dir_available: bool
    baseline_rows: int | None = None  # set on the check that recorded the baseline
    stable_events: int = 0
    processed: list[tuple[str, str]] = field(default_factory=list)  # (file name, status)


class GalleryWatcher:
    def __init__(
        self,
        gallery_dir: Path,
        store: GalleryStore,
        importer: GalleryImportService,
        *,
        poll_seconds: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._gallery_dir = Path(gallery_dir)
        self._store = store
        self._importer = importer
        self._poll_seconds = float(poll_seconds)
        self._clock = clock
        self._key = gallery_key(self._gallery_dir)
        self._trackers: dict[str, Tracker] = {}
        self._dir_available: bool | None = None
        self._last_poll_at: datetime | None = None
        self._running = False

    @property
    def key(self) -> str:
        return self._key

    @property
    def poll_seconds(self) -> float:
        return self._poll_seconds

    # -- one check -----------------------------------------------------------------

    def poll_once(self, stop: threading.Event | None = None) -> PollResult:
        now = self._clock()
        observed = self._scan()
        self._last_poll_at = now
        if observed is None:
            return PollResult(dir_available=False)
        result = PollResult(dir_available=True)
        try:
            if not self._store.baseline_done(self._key):
                result.baseline_rows = self._store.record_baseline(self._key, observed, now)
                logger.info("갤러리 기준선을 기록했습니다: %d개 파일", result.baseline_rows)
                return result
            self._trackers, events = advance_trackers(
                self._trackers, observed, now, timedelta(seconds=self._poll_seconds)
            )
            result.stable_events = len(events)
            for event in events:
                self._store.upsert_version(self._key, event, now)
            due = self._store.due_rows(self._key, now)
        except Exception as exc:  # DB trouble: try again on the next check (Req 1.8)
            logger.warning("갤러리 확인 실패 (%s)", type(exc).__name__)
            return result
        for row in due:
            if stop is not None and stop.is_set():
                break
            status = self._process_row(row.id, row.file_name)
            if status is not None:
                result.processed.append((row.file_name, status))
        return result

    def _scan(self) -> dict[str, Signature] | None:
        """Top-level candidates → signatures, or None when the folder is unusable."""
        observed: dict[str, Signature] = {}
        try:
            with os.scandir(self._gallery_dir) as entries:
                for entry in entries:
                    try:
                        is_symlink = entry.is_symlink()
                        is_regular = entry.is_file(follow_symlinks=False)
                    except OSError:
                        continue
                    if not is_gallery_candidate(entry.name, is_regular_file=is_regular, is_symlink=is_symlink):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        observed[entry.name] = Signature(size=info.st_size, mtime_ns=info.st_mtime_ns)
        except OSError:
            self._set_available(False)
            return None
        self._set_available(True)
        return observed

    def _set_available(self, available: bool) -> None:
        previous = self._dir_available
        self._dir_available = available
        if available == previous:
            return
        if not available:
            # No server path in the log (design "경로·비밀값·위치 비노출").
            logger.warning("갤러리 폴더를 사용할 수 없습니다. 다음 확인에서 다시 시도합니다.")
        elif previous is False:
            logger.info("갤러리 폴더를 다시 사용할 수 있습니다.")

    def _process_row(self, row_id: int, file_name: str) -> str | None:
        now = self._clock()
        try:
            if not self._store.claim(row_id, now):
                return None  # another process took it (Req 1.7)
            row = self._store.get(row_id)
            if row is None:
                return None
        except Exception as exc:
            logger.warning("갤러리 처리권 확보 실패: %s (%s)", file_name, type(exc).__name__)
            return None
        try:
            outcome = self._importer.process(row, now)
        except Exception as exc:  # the importer itself should not raise; keep the loop alive
            logger.warning("갤러리 사진 처리 예외: %s (%s)", file_name, type(exc).__name__)
            status, next_attempt_at = failure_transition("STORAGE_ERROR", max(row.attempts, 1), now)
            outcome = ImportOutcome(
                status=status, stage="storage", code="STORAGE_ERROR", next_attempt_at=next_attempt_at
            )
        try:
            self._store.finish(row_id, outcome, self._clock())
        except Exception as exc:
            logger.warning("갤러리 처리 결과 저장 실패: %s (%s)", file_name, type(exc).__name__)
        return outcome.status

    # -- loop and status ---------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        """Poll until ``stop`` is set (the lifespan thread or the CLI)."""
        self._running = True
        try:
            while not stop.is_set():
                try:
                    self.poll_once(stop)
                except Exception as exc:  # never let the thread die (Req 1.8)
                    logger.warning("갤러리 감시 반복 중 예외 (%s)", type(exc).__name__)
                if stop.wait(self._poll_seconds):
                    break
        finally:
            self._running = False

    def status(self) -> WatcherStatus:
        counts = self._store.counts(self._key)
        return WatcherStatus(
            enabled=True,
            running=self._running,
            dir_available=bool(self._dir_available),
            last_poll_at=self._last_poll_at,
            pending=counts["pending"] + counts["processing"] + counts["retry_wait"],
            uncertain=counts["uncertain"],
        )
