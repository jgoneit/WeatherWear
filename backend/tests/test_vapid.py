"""VAPID_Key_Store (Phase 2 tasks 4.7, Req 15.1, 15.2, 15.3, 15.10, 15.11, 18.7)."""

from __future__ import annotations

import logging
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import RedactingFilter
from app.notifications.push_store import b64url_decode
from app.notifications.vapid import (
    PRIVATE_NAME,
    PUBLIC_NAME,
    VapidKeyStore,
    VapidKeyUnavailable,
    public_key_b64url,
)
from tests.notifications_support import FakeClock

STAMP_NOW = datetime(2026, 11, 14, 1, 2, 3, tzinfo=UTC)


def load_private(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return key


def test_vapid_key_created_once_with_0600(tmp_path: Path) -> None:
    key_dir = tmp_path / "secrets"
    store = VapidKeyStore(key_dir)
    keys = store.ensure()

    private = key_dir / PRIVATE_NAME
    assert keys.private_key_path == private
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    pem = private.read_text()
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")  # PKCS8
    assert isinstance(load_private(private).curve, ec.SECP256R1)

    raw = b64url_decode(keys.public_key_b64url)
    assert raw is not None and len(raw) == 65 and raw[0] == 0x04
    assert "=" not in keys.public_key_b64url
    assert (key_dir / PUBLIC_NAME).read_text().strip() == keys.public_key_b64url
    assert public_key_b64url(load_private(private)) == keys.public_key_b64url

    # Second store (a restart) loads the same key and creates nothing new.
    again = VapidKeyStore(key_dir).ensure()
    assert again == keys
    assert private.read_text() == pem
    assert sorted(p.name for p in key_dir.iterdir()) == [PRIVATE_NAME, PUBLIC_NAME]


def test_public_file_is_rewritten_from_private_key(tmp_path: Path) -> None:
    store = VapidKeyStore(tmp_path)
    keys = store.ensure()
    (tmp_path / PUBLIC_NAME).write_text("stale\n")
    assert VapidKeyStore(tmp_path).ensure() == keys
    assert (tmp_path / PUBLIC_NAME).read_text().strip() == keys.public_key_b64url


def test_concurrent_ensure_makes_one_key(tmp_path: Path) -> None:
    stores = [VapidKeyStore(tmp_path) for _ in range(6)]  # separate stores ~ separate processes
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(stores))

    def run(store: VapidKeyStore) -> None:
        try:
            barrier.wait()
            results.append(store.ensure().public_key_b64url)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(results)) == 1
    assert public_key_b64url(load_private(tmp_path / PRIVATE_NAME)) == results[0]
    assert not [p for p in tmp_path.iterdir() if ".invalid-" in p.name or p.name.endswith(".tmp")]


def test_corrupt_key_renamed_and_subscriptions_cleared(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / PRIVATE_NAME).write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n")
    rotated: list[str] = []
    store = VapidKeyStore(tmp_path, FakeClock(STAMP_NOW), on_rotated=rotated.append)
    caplog.set_level(logging.WARNING, logger="app.notifications.vapid")
    keys = store.ensure()

    invalid = tmp_path / f"{PRIVATE_NAME}.invalid-20261114010203"
    assert invalid.read_text().startswith("-----BEGIN PRIVATE KEY-----\nnot a key")  # kept, not deleted
    assert public_key_b64url(load_private(tmp_path / PRIVATE_NAME)) == keys.public_key_b64url
    assert rotated == [keys.public_key_b64url]
    assert any(r.levelno == logging.WARNING and "invalid-" in r.getMessage() for r in caplog.records)


def test_wrong_curve_key_is_rotated(tmp_path: Path) -> None:
    other = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (tmp_path / PRIVATE_NAME).write_bytes(other)
    clock = FakeClock(STAMP_NOW)
    keys = VapidKeyStore(tmp_path, clock).ensure()
    assert (tmp_path / f"{PRIVATE_NAME}.invalid-20261114010203").read_bytes() == other
    assert isinstance(load_private(keys.private_key_path).curve, ec.SECP256R1)


def test_second_corruption_same_second_gets_unique_name(tmp_path: Path) -> None:
    clock = FakeClock(STAMP_NOW)
    for _ in range(2):
        (tmp_path / PRIVATE_NAME).write_text("garbage")
        VapidKeyStore(tmp_path, clock).ensure()
    names = sorted(p.name for p in tmp_path.iterdir() if ".invalid-" in p.name)
    assert names == [f"{PRIVATE_NAME}.invalid-20261114010203", f"{PRIVATE_NAME}.invalid-20261114010203-1"]


def test_vapid_dir_unwritable_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file")
    store = VapidKeyStore(blocker / "secrets")
    with pytest.raises(VapidKeyUnavailable) as excinfo:
        store.ensure()
    assert str(blocker) not in str(excinfo.value)
    assert store.current() is None and store.secret_strings() == []


def test_key_values_never_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    (tmp_path / PRIVATE_NAME).write_text("garbage-key-material-1234567890")
    store = VapidKeyStore(tmp_path, FakeClock(STAMP_NOW))
    store.ensure()
    pem = (tmp_path / PRIVATE_NAME).read_text()
    body = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
    text = caplog.text
    for start in range(0, len(body) - 16, 8):
        assert body[start : start + 16] not in text
    assert "garbage-key-material" not in text


def test_secret_strings_redact_pem(tmp_path: Path) -> None:
    secrets_seen: list[list[str]] = []
    store = VapidKeyStore(tmp_path, on_secrets=secrets_seen.append)
    assert store.secret_strings() == []
    store.ensure()
    pem = (tmp_path / PRIVATE_NAME).read_text()
    strings = store.secret_strings()
    assert secrets_seen == [strings]
    redacting = RedactingFilter(strings)
    body_line = pem.splitlines()[1]
    assert redacting.redact(f"leak {pem.strip()} end") == "leak *** end"
    assert body_line not in redacting.redact(f"line {body_line}")
    joined = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
    assert joined not in redacting.redact(joined)
    assert store.public_path.read_text().strip() not in strings
