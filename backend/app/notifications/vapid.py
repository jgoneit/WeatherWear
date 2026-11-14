"""VAPID_Key_Store: the P-256 key pair for Web Push (design.md §16, Req 15.1~15.3, 15.10, 15.11).

Files in VAPID_KEY_DIR (git-ignored, Req 18.6):

- ``vapid_private.pem``: PKCS8 PEM, mode 0600. The only source of truth.
- ``vapid_public.txt``: base64url (no padding) uncompressed point, 65 bytes
  starting with 0x04. Derived from the private key and rewritten when it differs.

``ensure()``:

1. A loadable private key → compute the public key and return it.
2. No private key → generate one and publish it without ever exposing a
   partially written file: the PEM is written to a unique temp file opened
   with ``O_CREAT|O_EXCL`` and mode 0600, then hard-linked to
   ``vapid_private.pem``. ``os.link`` never replaces an existing file, so when
   two processes start at once exactly one key wins and the loser loads it
   (Req 15.2). File systems without hard links fall back to opening
   ``vapid_private.pem`` itself with ``O_CREAT|O_EXCL``.
3. An unreadable or malformed private key is never deleted: it is renamed to
   ``vapid_private.pem.invalid-<UTC yyyymmddHHMMSS>`` with a WARNING, a new key
   is made as in 2., and ``on_rotated(new_public_key)`` lets the runtime delete
   subscriptions made with the old key (Req 15.3). ``on_rotated`` also runs
   when a key is generated into an empty folder, so subscriptions left from a
   key that was removed by hand are cleaned up too.

A folder that cannot be created or written raises ``VapidKeyUnavailable``
(API 503, Req 15.10). Key material never reaches a log line or an exception
message; ``secret_strings()`` lists it for the RedactingFilter (Req 18.7).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger(__name__)

PRIVATE_NAME = "vapid_private.pem"
PUBLIC_NAME = "vapid_public.txt"
INVALID_SUFFIX = ".invalid-"
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class VapidKeys:
    private_key_path: Path
    public_key_b64url: str  # 65-byte uncompressed point, base64url without padding


class VapidKeyUnavailable(Exception):
    """VAPID_KEY_DIR cannot hold a key (API 503 ``VAPID_KEY_UNAVAILABLE``)."""


class _InvalidKey(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def public_key_b64url(private_key: ec.EllipticCurvePrivateKey) -> str:
    point = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64url_encode(point)


def _pem_secrets(pem: str) -> list[str]:
    """The PEM text, its base64 body joined, and each body line (for redaction)."""
    lines = [line.strip() for line in pem.strip().splitlines() if line.strip()]
    body = [line for line in lines if not line.startswith("-----")]
    joined = "".join(body)
    return [s for s in dict.fromkeys([pem.strip(), joined, *body]) if s]


class VapidKeyStore:
    def __init__(
        self,
        key_dir: Path,
        clock: Callable[[], datetime] = _utc_now,
        *,
        on_rotated: Callable[[str], object] | None = None,
        on_secrets: Callable[[list[str]], object] | None = None,
    ) -> None:
        self._dir = Path(key_dir)
        self._clock = clock
        self._on_rotated = on_rotated
        self._on_secrets = on_secrets
        self._lock = threading.Lock()
        self._keys: VapidKeys | None = None
        self._secrets: list[str] = []

    @property
    def key_dir(self) -> Path:
        return self._dir

    @property
    def private_path(self) -> Path:
        return self._dir / PRIVATE_NAME

    @property
    def public_path(self) -> Path:
        return self._dir / PUBLIC_NAME

    def set_on_rotated(self, callback: Callable[[str], object] | None) -> None:
        self._on_rotated = callback

    def current(self) -> VapidKeys | None:
        """Keys loaded by an earlier ``ensure()`` in this process, without touching files."""
        return self._keys

    @property
    def ready(self) -> bool:
        return self._keys is not None

    def secret_strings(self) -> list[str]:
        """Private key strings for the RedactingFilter; empty before the first ``ensure()``."""
        return list(self._secrets)

    def ensure(self) -> VapidKeys:
        """Load or create the key pair. Raises ``VapidKeyUnavailable``."""
        with self._lock:
            try:
                self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                raise VapidKeyUnavailable(f"VAPID_KEY_DIR를 만들 수 없습니다 ({type(exc).__name__})") from None
            if not self._dir.is_dir():
                raise VapidKeyUnavailable("VAPID_KEY_DIR가 폴더가 아닙니다")

            created = False
            for _ in range(_MAX_ATTEMPTS):
                try:
                    pem = self._read_private()
                except FileNotFoundError:
                    pem = None
                except _InvalidKey:
                    self._quarantine()
                    pem = None
                if pem is not None:
                    keys = self._publish(pem)
                    if created and self._on_rotated is not None:
                        self._notify_rotated(keys.public_key_b64url)
                    return keys
                created = self._create() or created
            raise VapidKeyUnavailable("VAPID 키를 준비하지 못했습니다")

    # --- steps ------------------------------------------------------------------

    def _read_private(self) -> str:
        """PEM text of a valid P-256 private key. FileNotFoundError when absent."""
        try:
            data = self.private_path.read_bytes()
        except FileNotFoundError:
            raise
        except OSError:
            raise _InvalidKey() from None
        try:
            key = serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError):
            raise _InvalidKey() from None
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise _InvalidKey()
        return data.decode("ascii")

    def _quarantine(self) -> None:
        stamp = self._clock().astimezone(UTC).strftime("%Y%m%d%H%M%S")
        target = self._dir / f"{PRIVATE_NAME}{INVALID_SUFFIX}{stamp}"
        counter = 1
        while target.exists():
            target = self._dir / f"{PRIVATE_NAME}{INVALID_SUFFIX}{stamp}-{counter}"
            counter += 1
        try:
            os.rename(self.private_path, target)
        except FileNotFoundError:
            return  # another process already moved it
        except OSError as exc:
            raise VapidKeyUnavailable(f"손상된 VAPID 키를 옮기지 못했습니다 ({type(exc).__name__})") from None
        logger.warning("VAPID 개인키를 읽을 수 없어 %s(으)로 옮기고 새 키를 만듭니다.", target.name)

    def _create(self) -> bool:
        """Write a new private key unless one exists. True when this call created it."""
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        tmp = self._dir / f".{PRIVATE_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            self._write_exclusive(tmp, pem)
            try:
                os.link(tmp, self.private_path)
            except FileExistsError:
                return False
            except OSError:
                # No hard links on this file system: exclusive create in place.
                try:
                    self._write_exclusive(self.private_path, pem)
                except FileExistsError:
                    return False
        except OSError as exc:
            raise VapidKeyUnavailable(f"VAPID 키를 쓸 수 없습니다 ({type(exc).__name__})") from None
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        logger.info("새 VAPID 키 쌍을 만들었습니다.")
        return True

    @staticmethod
    def _write_exclusive(path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish(self, pem: str) -> VapidKeys:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        public = public_key_b64url(key)
        try:
            stored = self.public_path.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            stored = None
        if stored != public:
            try:
                tmp = self._dir / f".{PUBLIC_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
                tmp.write_text(public + "\n", encoding="ascii")
                os.replace(tmp, self.public_path)
            except OSError as exc:
                # The public file is only a copy; the private key is what counts.
                logger.warning("VAPID 공개키 파일을 쓰지 못했습니다 (%s)", type(exc).__name__)
        keys = VapidKeys(private_key_path=self.private_path, public_key_b64url=public)
        if keys != self._keys:
            self._keys = keys
            self._secrets = _pem_secrets(pem)
            if self._on_secrets is not None:
                try:
                    self._on_secrets(self.secret_strings())
                except Exception as exc:
                    logger.warning("VAPID 비밀값 등록 콜백 실패 (%s)", type(exc).__name__)
        return keys

    def _notify_rotated(self, public_key: str) -> None:
        try:
            self._on_rotated(public_key)  # type: ignore[misc]
        except Exception as exc:
            logger.warning("VAPID 키 교체 후 구독 정리에 실패했습니다 (%s)", type(exc).__name__)
