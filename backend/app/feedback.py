"""Feedback rules (design.md §8).

``effective_vote`` is a pure function so the Effective_Vote rule lives in one
place: the repository, the recommendation input and the API all use it.
``FeedbackService`` stores user feedback through ``OutfitRepository``.

``app.repository`` imports this module at load time, so the repository types
are imported only for type checking here to avoid a circular import.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, get_args

from app.domain import ThermalVote

if TYPE_CHECKING:
    from app.repository import FeedbackRecord, OutfitRepository

logger = logging.getLogger(__name__)

_VALID_VOTES: frozenset[str] = frozenset(get_args(ThermalVote))


class FeedbackLike(Protocol):
    """Anything with the fields Effective_Vote needs (e.g. ``FeedbackRecord``)."""

    @property
    def id(self) -> int: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def thermal_vote(self) -> ThermalVote: ...


def effective_vote(records: Iterable[FeedbackLike]) -> ThermalVote | None:
    """Thermal vote of the latest record by ``(created_at, id)`` (Req 9.4).

    ``created_at`` values must be timezone-aware so they compare as instants.
    A larger ``id`` breaks ties on equal ``created_at``. The record's source
    (``user_action`` or ``demo_seed``) does not matter. Returns None when there
    are no records.
    """
    latest: FeedbackLike | None = None
    for record in records:
        if latest is None or (record.created_at, record.id) > (latest.created_at, latest.id):
            latest = record
    return None if latest is None else latest.thermal_vote


# ---------------------------------------------------------------------------
# FeedbackService
# ---------------------------------------------------------------------------


class OutfitNotFound(Exception):
    """No Outfit with the given id exists (API: 404 ``OUTFIT_NOT_FOUND``, Req 9.3)."""

    def __init__(self, outfit_id: int) -> None:
        super().__init__(f"outfit {outfit_id} not found")
        self.outfit_id = outfit_id


class FeedbackSaveFailed(Exception):
    """Storing the feedback row failed and was rolled back
    (API: 500 ``FEEDBACK_SAVE_FAILED``, Req 9.8)."""

    def __init__(self, outfit_id: int) -> None:
        super().__init__(f"failed to save feedback for outfit {outfit_id}")
        self.outfit_id = outfit_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FeedbackService:
    """Stores user Thermal_Votes as new feedback rows (design.md §8).

    Every submit is one INSERT with ``source='user_action'``; earlier rows are
    never updated or deleted (Req 9.5). Only the vote is stored, so extra
    request fields cannot reach the database (Req 9.7).
    """

    def __init__(
        self, repo: OutfitRepository, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._repo = repo
        self._clock = clock

    def submit(self, outfit_id: int, vote: ThermalVote) -> FeedbackRecord:
        """Save one ``user_action`` feedback row and return it (Req 9.1).

        Raises ``ValueError`` for a vote outside cold/comfortable/hot (the API
        rejects those with 422 before calling this), ``OutfitNotFound`` when the
        Outfit does not exist (Req 9.3), and ``FeedbackSaveFailed`` when the
        insert fails. The repository rolls the transaction back, so no partial
        row is left and the Effective_Vote stays unchanged (Req 9.8).
        """
        if vote not in _VALID_VOTES:
            raise ValueError("thermal_vote must be one of cold, comfortable, hot")
        if self._repo.get_outfit(outfit_id) is None:
            raise OutfitNotFound(outfit_id)

        created_at = self._clock().astimezone(UTC)  # microsecond precision kept
        try:
            return self._repo.add_feedback(outfit_id, vote, "user_action", created_at)
        except Exception as exc:
            logger.error("feedback save failed for outfit %s: %s", outfit_id, type(exc).__name__)
            raise FeedbackSaveFailed(outfit_id) from exc
