"""GalleryStore (tasks 3.3, Req 2.4, 2.5, 1.7, 3.3, 6.8, 17.3)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from app.db_automation import AppState, GalleryFile
from app.gallery.rules import Signature, StableEvent
from app.gallery.store import LEASE, GalleryStore, ImportOutcome, gallery_key
from app.schemas import ScanDecision
from tests.gallery_support import T0, make_engine

KEY = "k" * 16
SHA = "a" * 64


def event(name: str, size: int = 10, mtime_ns: int = 1, first_seen=T0) -> StableEvent:
    return StableEvent(name=name, signature=Signature(size, mtime_ns), first_seen=first_seen)


def test_gallery_key_is_stable_and_short(tmp_path: Path) -> None:
    (tmp_path / "g").mkdir()
    assert gallery_key(tmp_path / "g") == gallery_key(tmp_path / "g" / ".." / "g")
    assert len(gallery_key(tmp_path / "g")) == 16
    assert gallery_key(tmp_path / "g") != gallery_key(tmp_path)


def test_baseline_not_redone_for_same_gallery_dir(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    assert not store.baseline_done(KEY)
    observed = {"a.png": Signature(1, 1), "b.jpg": Signature(2, 2)}
    assert store.record_baseline(KEY, observed, T0) == 2
    assert store.baseline_done(KEY)
    # A second start (or a second process) neither rewrites nor adds rows.
    assert store.record_baseline(KEY, {"c.png": Signature(3, 3)}, T0 + timedelta(hours=1)) == 0
    with Session(store.engine) as session:
        rows = session.exec(select(GalleryFile)).all()
        assert sorted((r.file_name, r.status) for r in rows) == [("a.png", "baseline"), ("b.jpg", "baseline")]
        assert session.get(AppState, f"gallery.baseline:{KEY}") is not None
    # A different Gallery_Dir has its own baseline.
    assert not store.baseline_done("other-key-000000")


def test_empty_folder_baseline_is_marked(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    assert store.record_baseline(KEY, {}, T0) == 0
    assert store.baseline_done(KEY)


def test_upsert_ignores_same_signature_including_baseline(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    store.record_baseline(KEY, {"old.png": Signature(10, 1)}, T0)
    assert store.upsert_version(KEY, event("old.png"), T0) is None
    row = store.upsert_version(KEY, event("new.png"), T0)
    assert row is not None and row.status == "pending" and row.attempts == 0
    assert store.upsert_version(KEY, event("new.png"), T0 + timedelta(seconds=5)) is None


def test_changed_signature_is_a_new_version(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    store.record_baseline(KEY, {"old.png": Signature(10, 1)}, T0)
    row = store.upsert_version(KEY, event("old.png", size=20, mtime_ns=2), T0 + timedelta(seconds=9))
    assert row is not None and row.status == "pending" and row.signature == Signature(20, 2)

    # A failed version does not pass its attempts or results to the next one (Req 2.3).
    assert store.claim(row.id, T0 + timedelta(seconds=10))
    store.finish(row.id, ImportOutcome(status="failed", sha256=SHA, stage="validation",
                                       code="INVALID_IMAGE_FORMAT"), T0 + timedelta(seconds=11))
    newer = store.upsert_version(KEY, event("old.png", size=30, mtime_ns=3), T0 + timedelta(seconds=20))
    assert newer is not None
    assert (newer.id, newer.status, newer.attempts, newer.sha256, newer.last_error_code) == (
        row.id, "pending", 0, None, None,
    )


def test_due_rows_order_and_conditions(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    b = store.upsert_version(KEY, event("b.png", first_seen=T0), T0)
    a = store.upsert_version(KEY, event("a.png", first_seen=T0), T0)
    c = store.upsert_version(KEY, event("c.png", first_seen=T0 - timedelta(seconds=1)), T0)
    assert [r.file_name for r in store.due_rows(KEY, T0)] == ["c.png", "a.png", "b.png"]

    now = T0 + timedelta(seconds=1)
    assert store.claim(a.id, now)
    store.finish(a.id, ImportOutcome(status="retry_wait", code="VISION_TIMEOUT", stage="analysis",
                                     next_attempt_at=now + timedelta(minutes=1)), now)
    assert store.claim(b.id, now)  # processing with a live lease
    assert [r.file_name for r in store.due_rows(KEY, now)] == ["c.png"]
    later = now + timedelta(minutes=1)
    assert [r.file_name for r in store.due_rows(KEY, later)] == ["c.png", "a.png"]
    assert c is not None


def test_claim_once_between_two_stores(tmp_path: Path) -> None:
    # Two engines on the same file stand in for the API server and the CLI (Req 1.7).
    server = GalleryStore(make_engine(tmp_path, "shared.db"))
    cli = GalleryStore(make_engine(tmp_path, "shared.db"))
    row = server.upsert_version(KEY, event("x.jpg"), T0)
    assert cli.upsert_version(KEY, event("x.jpg"), T0) is None
    assert [r.id for r in cli.due_rows(KEY, T0)] == [row.id]
    assert server.claim(row.id, T0) is True
    assert cli.claim(row.id, T0) is False
    assert cli.get(row.id).attempts == 1


def test_expired_lease_reclaimed(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    row = store.upsert_version(KEY, event("x.jpg"), T0)
    assert store.claim(row.id, T0)
    assert not store.claim(row.id, T0 + LEASE - timedelta(seconds=1))
    assert store.due_rows(KEY, T0 + LEASE - timedelta(seconds=1)) == []
    assert [r.id for r in store.due_rows(KEY, T0 + LEASE)] == [row.id]
    assert store.claim(row.id, T0 + LEASE)
    assert store.get(row.id).attempts == 2  # the crashed attempt counts (Req 6.11)


def test_finish_requires_processing(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    row = store.upsert_version(KEY, event("x.jpg"), T0)
    assert not store.finish(row.id, ImportOutcome(status="imported", outfit_id=None), T0)
    assert store.claim(row.id, T0)
    decision = ScanDecision(file_name="x.jpg", photo_type="uncertain", confidence=0.4, reason="r" * 200)
    assert store.finish(row.id, ImportOutcome(status="uncertain", sha256=SHA, decision=decision,
                                              capture_origin="estimated"), T0)
    stored = store.get(row.id)
    assert (stored.status, stored.photo_type, stored.photo_type_confidence, stored.capture_origin) == (
        "uncertain", "uncertain", 0.4, "estimated",
    )
    assert len(stored.reason) == 200 and stored.lease_until is None


def test_reset_for_reanalyze_rules(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    outcomes = {
        "u.jpg": ImportOutcome(status="uncertain", sha256=SHA,
                               decision=ScanDecision(file_name="u.jpg", photo_type="uncertain",
                                                     confidence=0.0, reason="unknown")),
        "f.jpg": ImportOutcome(status="failed", stage="analysis", code="VISION_TIMEOUT"),
        "i.jpg": ImportOutcome(status="imported", sha256="b" * 64),
        "s.jpg": ImportOutcome(status="skipped_non_outfit", sha256="c" * 64),
        "d.jpg": ImportOutcome(status="duplicate", sha256="d" * 64),
    }
    ids = {}
    for name, outcome in outcomes.items():
        row = store.upsert_version(KEY, event(name), T0)
        store.claim(row.id, T0)
        store.claim(row.id, T0)
        store.finish(row.id, outcome, T0)
        ids[name] = row.id
    pending = store.upsert_version(KEY, event("p.jpg"), T0)
    store.record_baseline(KEY, {"base.jpg": Signature(1, 1)}, T0)

    assert store.reset_for_reanalyze(ids["u.jpg"]) == "ok"
    assert store.reset_for_reanalyze(ids["f.jpg"]) == "ok"
    for name in ("i.jpg", "s.jpg", "d.jpg"):
        assert store.reset_for_reanalyze(ids[name]) == "not_allowed"
    assert store.reset_for_reanalyze(pending.id) == "not_allowed"
    assert store.reset_for_reanalyze(9999) == "not_found"
    reset = store.get(ids["u.jpg"])
    assert (reset.status, reset.attempts, reset.photo_type, reset.last_error_code) == ("pending", 0, None, None)
    assert store.get(ids["f.jpg"]).last_error_code is None


def test_active_with_sha(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    first = store.upsert_version(KEY, event("1.jpg"), T0)
    second = store.upsert_version(KEY, event("2.jpg"), T0)
    store.claim(first.id, T0)
    store.claim(second.id, T0)
    store.record_sha(first.id, SHA, T0)
    store.record_sha(second.id, SHA, T0)
    # Two in-flight rows: only the later one sees the earlier one.
    assert store.active_with_sha(SHA, exclude_id=second.id)
    assert not store.active_with_sha(SHA, exclude_id=first.id)
    store.finish(first.id, ImportOutcome(status="imported", sha256=SHA, outfit_id=None), T0)
    assert store.active_with_sha(SHA, exclude_id=second.id)
    assert not store.active_with_sha("e" * 64, exclude_id=second.id)


def test_list_imports_excludes_baseline_and_orders(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    store.record_baseline(KEY, {"base.jpg": Signature(1, 1)}, T0)
    rows = [store.upsert_version(KEY, event(f"{i}.jpg"), T0 + timedelta(seconds=i)) for i in range(3)]
    store.claim(rows[0].id, T0 + timedelta(seconds=10))
    store.finish(rows[0].id, ImportOutcome(status="failed", code="X"), T0 + timedelta(seconds=10))
    listed = store.list_imports(20)
    assert [r.file_name for r in listed] == ["0.jpg", "2.jpg", "1.jpg"]
    assert [r.file_name for r in store.list_imports(20, "failed")] == ["0.jpg"]
    assert [r.file_name for r in store.list_imports(1)] == ["0.jpg"]
    counts = store.counts(KEY)
    assert counts["baseline"] == 1 and counts["pending"] == 2 and counts["failed"] == 1


def test_user_photos_put_lookup_delete(tmp_path: Path) -> None:
    store = GalleryStore(make_engine(tmp_path))
    store.put_user_photo(SHA, "x.jpg", "x.jpg", True, T0)
    assert store.user_photo_file(SHA) == "x.jpg"
    assert store.user_photo_sha_for_file("x.jpg") == SHA
    store.put_user_photo(SHA, "x_aaaaaaaa.jpg", "x.jpg", False, T0)
    assert store.user_photo_file(SHA) == "x_aaaaaaaa.jpg"
    store.delete_user_photo(SHA)
    assert store.user_photo_file(SHA) is None
