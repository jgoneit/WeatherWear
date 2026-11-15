"""Push_Subscription_Store (design.md §17, Req 15.5, 15.6).

``validate_subscription`` applies the format rules of Req 15.5 to a browser
``PushSubscription.toJSON()`` body: an ``https://`` endpoint of at most 2,048
characters, ``keys.p256dh`` base64url of a 65-byte uncompressed P-256 point
(first byte 0x04) and ``keys.auth`` base64url of 16 bytes. Padding is optional.
Reasons name the field and rule only, never the value.

Only endpoints on a known push service host are accepted (review fix 2):
the server sends ``POST`` requests to stored endpoints (``/api/push/test``,
the daily brief), so an arbitrary https host would let anyone who can reach
the API make the server call that host (SSRF). ``DEFAULT_PUSH_HOSTS`` covers
the browser push services; PUSH_ALLOWED_HOSTS adds more. Endpoints with
user info or a port other than 443 are refused too.

Subscriptions are keyed by ``endpoint`` (UNIQUE): registering the same
endpoint again updates its keys instead of adding a row. Endpoints and keys
are never logged; logs carry the subscription id and the endpoint host name
only (Req 18.7).
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.db import to_utc_iso
from app.db_automation import PushSubscription
from app.schemas_automation import PushSubscriptionIn

logger = logging.getLogger(__name__)

MAX_ENDPOINT_LENGTH = 2048
P256DH_BYTES = 65
AUTH_BYTES = 16
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")

# Browser push services (review fix 2). "*.<domain>" matches any subdomain.
DEFAULT_PUSH_HOSTS: tuple[str, ...] = (
    "fcm.googleapis.com",  # Chrome, Edge (Chromium), Opera, Samsung Internet
    "android.googleapis.com",  # older Chromium (GCM) endpoints
    "updates.push.services.mozilla.com",  # Firefox
    "*.notify.windows.com",  # Windows Push Notification Services
    "web.push.apple.com",  # Safari (macOS, iOS home screen web apps)
)


class InvalidSubscription(ValueError):
    """A subscription that fails ``validate_subscription`` (API 422 ``PUSH_SUBSCRIPTION_INVALID``)."""


@dataclass(frozen=True)
class PushSubscriptionRow:
    id: int
    endpoint: str
    p256dh: str
    auth: str
    vapid_public_key: str

    def __repr__(self) -> str:  # never print the endpoint or keys
        return f"PushSubscriptionRow(id={self.id}, host={endpoint_host(self.endpoint)!r})"


def endpoint_host(endpoint: str) -> str:
    """Host name of an endpoint, the only part of it that may be logged."""
    try:
        return urlparse(endpoint).hostname or "?"
    except ValueError:
        return "?"


def host_allowed(host: str, patterns: Sequence[str]) -> bool:
    """``host`` equals a pattern, or is a subdomain of a ``*.<domain>`` pattern."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def endpoint_allowed(endpoint: str, patterns: Sequence[str]) -> bool:
    """https, no user info, default port, and a host from ``patterns``."""
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443):
        return False
    return host_allowed(parsed.hostname or "", patterns)


def b64url_decode(value: str) -> bytes | None:
    """Strict base64url decode (padding optional); None when not base64url."""
    if not isinstance(value, str) or not _BASE64URL.match(value):
        return None
    stripped = value.rstrip("=")
    if len(stripped) % 4 == 1:
        return None
    try:
        return base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
    except (binascii.Error, ValueError):
        return None


def validate_subscription(
    payload: PushSubscriptionIn, allowed_hosts: Sequence[str] = DEFAULT_PUSH_HOSTS
) -> str | None:
    """None when valid, otherwise a short reason without the value (Req 15.5)."""
    endpoint = payload.endpoint
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return "endpoint must be an https URL"
    if len(endpoint) > MAX_ENDPOINT_LENGTH:
        return f"endpoint must be at most {MAX_ENDPOINT_LENGTH} characters"
    if any(ch.isspace() for ch in endpoint) or endpoint_host(endpoint) in {"", "?"}:
        return "endpoint must be an https URL"
    if not endpoint_allowed(endpoint, allowed_hosts):
        return "endpoint must be on a known push service host"
    p256dh = b64url_decode(payload.keys.p256dh)
    if p256dh is None or len(p256dh) != P256DH_BYTES or p256dh[0] != 0x04:
        return f"keys.p256dh must be base64url of a {P256DH_BYTES}-byte uncompressed point"
    auth = b64url_decode(payload.keys.auth)
    if auth is None or len(auth) != AUTH_BYTES:
        return f"keys.auth must be base64url of {AUTH_BYTES} bytes"
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _row(model: PushSubscription) -> PushSubscriptionRow:
    assert model.id is not None
    return PushSubscriptionRow(
        id=model.id,
        endpoint=model.endpoint,
        p256dh=model.p256dh,
        auth=model.auth,
        vapid_public_key=model.vapid_public_key,
    )


class PushSubscriptionStore:
    def __init__(
        self,
        engine: Engine,
        clock: Callable[[], datetime] = _utc_now,
        *,
        allowed_hosts: Sequence[str] = DEFAULT_PUSH_HOSTS,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self.allowed_hosts: tuple[str, ...] = tuple(allowed_hosts)

    def upsert(self, sub: PushSubscriptionIn, public_key: str, now: datetime | None = None) -> int:
        """Insert or update by endpoint; returns the subscription id.

        Raises ``InvalidSubscription`` (nothing stored) when the body fails the rules.
        """
        reason = validate_subscription(sub, self.allowed_hosts)
        if reason is not None:
            raise InvalidSubscription(reason)
        stamp = to_utc_iso(now or self._clock())
        values = {
            "endpoint": sub.endpoint,
            "p256dh": sub.keys.p256dh,
            "auth": sub.keys.auth,
            "vapid_public_key": public_key,
            "created_at": stamp,
            "failure_count": 0,
        }
        statement = sqlite_insert(PushSubscription).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["endpoint"],
            set_={
                "p256dh": statement.excluded.p256dh,
                "auth": statement.excluded.auth,
                "vapid_public_key": statement.excluded.vapid_public_key,
                "failure_count": 0,
                "last_failure_at": None,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(statement)
            sub_id = conn.execute(
                select(PushSubscription.id).where(PushSubscription.endpoint == sub.endpoint)
            ).scalar_one()
        logger.info("푸시 구독을 저장했습니다: id=%d host=%s", sub_id, endpoint_host(sub.endpoint))
        return int(sub_id)

    def delete(self, sub_id: int) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(delete(PushSubscription).where(PushSubscription.id == sub_id))
        deleted = (result.rowcount or 0) > 0
        if deleted:
            logger.info("푸시 구독을 지웠습니다: id=%d", sub_id)
        return deleted

    def get(self, sub_id: int) -> PushSubscriptionRow | None:
        with Session(self._engine) as session:
            model = session.get(PushSubscription, sub_id)
            return _row(model) if model is not None else None

    def list_active(self) -> list[PushSubscriptionRow]:
        with Session(self._engine) as session:
            models = session.execute(select(PushSubscription).order_by(PushSubscription.id)).scalars()  # type: ignore[arg-type]
            return [_row(m) for m in models]

    def delete_not_matching_key(self, public_key: str) -> int:
        """Delete subscriptions made with another VAPID public key (key rotation, Req 15.3)."""
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(PushSubscription).where(PushSubscription.vapid_public_key != public_key)
            )
        count = result.rowcount or 0
        if count:
            logger.warning("이전 VAPID 키로 만든 푸시 구독 %d건을 지웠습니다.", count)
        return count

    def count(self) -> int:
        with Session(self._engine) as session:
            return int(session.execute(select(func.count()).select_from(PushSubscription)).scalar_one())

    def mark_success(self, sub_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(PushSubscription)
                .where(PushSubscription.id == sub_id)
                .values(last_success_at=to_utc_iso(self._clock()), failure_count=0)
            )

    def mark_failure(self, sub_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(PushSubscription)
                .where(PushSubscription.id == sub_id)
                .values(
                    last_failure_at=to_utc_iso(self._clock()),
                    failure_count=PushSubscription.failure_count + 1,
                )
            )
