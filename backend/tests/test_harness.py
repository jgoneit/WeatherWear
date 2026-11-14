"""Sanity checks for the conftest harness (Req 15.1, 17.5)."""

import os
import socket

import pytest
from hypothesis import settings


def test_external_connection_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(RuntimeError, match="blocked"):
            s.connect(("93.184.216.34", 443))


def test_loopback_connection_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass


def test_gemini_api_key_removed() -> None:
    assert "GEMINI_API_KEY" not in os.environ


def test_hypothesis_ci_profile_loaded() -> None:
    assert settings.default.max_examples == 100
    assert settings.default.deadline is None
