"""Object particle 을/를 (design.md §14, Req 11.8, 11.15). Pure function."""

from __future__ import annotations

from typing import Literal

HANGUL_FIRST = 0xAC00  # 가
HANGUL_LAST = 0xD7A3  # 힣
FINAL_CONSONANTS = 28  # 27 final consonants + "no final consonant"

ObjectParticle = Literal["을", "를", "을(를)"]


def object_particle(word: str) -> ObjectParticle:
    """"을" after a Hangul syllable with a final consonant, "를" after one without,
    "을(를)" when the last character is not a Hangul syllable (or ``word`` is empty)."""
    if not word:
        return "을(를)"
    code = ord(word[-1])
    if not HANGUL_FIRST <= code <= HANGUL_LAST:
        return "을(를)"
    return "을" if (code - HANGUL_FIRST) % FINAL_CONSONANTS != 0 else "를"
