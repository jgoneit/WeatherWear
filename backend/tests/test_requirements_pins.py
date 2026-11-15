"""``backend/requirements.txt`` pins every package exactly (Phase 1 Req 17.7, Phase 2 tasks 7.4)."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)$")


def pins() -> dict[str, str]:
    found: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN.fullmatch(line)
        assert match, f"not an exact pin: {line!r}"
        found[match.group(1).lower()] = match.group(2)
    return found


def test_every_requirement_is_an_exact_pin_of_the_installed_version() -> None:
    for name, version in pins().items():
        assert importlib.metadata.version(name) == version, name


def test_phase2_web_push_dependencies_pinned() -> None:
    found = pins()
    assert found["pywebpush"] == "2.5.0"
    assert found["cryptography"] == "50.0.2"
    assert "requests" in found
