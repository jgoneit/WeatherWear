"""Shared pytest harness.

- Blocks every outbound network connection except loopback, so no test can
  reach Gemini or Open-Meteo (Req 15.1, 17.5).
- Removes GEMINI_API_KEY from the environment for every test.
- Registers and loads the Hypothesis ``ci`` profile.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest
from hypothesis import settings

settings.register_profile("ci", max_examples=100, deadline=None)
settings.load_profile("ci")

_ALLOWED_HOSTNAMES = {"localhost"}


def _is_local_address(address: object) -> bool:
    # AF_UNIX sockets use a str/bytes path and never leave the machine.
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode(errors="replace")
        if not isinstance(host, str):
            return False
        if host.lower() in _ALLOWED_HOSTNAMES:
            return True
        try:
            return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except ValueError:
            return False
    return False


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: object) -> None:
        if not _is_local_address(address):
            raise RuntimeError(f"External network access is blocked in tests: {address!r}")
        return original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if not _is_local_address(address):
            raise RuntimeError(f"External network access is blocked in tests: {address!r}")
        return original_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture(autouse=True)
def _remove_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# Phase 2 (tasks 7.1): the default Phase 2 folders resolve into the repository
# (data/user/, data/secrets/). A test that forgets to point them at tmp_path
# would write user photos or a VAPID key into the working tree; fail the run
# if one of them appears during the session.
_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
_GUARDED_DIRS = (_REPO_ROOT / "data" / "user", _REPO_ROOT / "data" / "secrets")


@pytest.fixture(scope="session", autouse=True)
def _no_writes_to_repo_phase2_dirs():
    existing = {path for path in _GUARDED_DIRS if path.exists()}
    yield
    created = [str(path.relative_to(_REPO_ROOT)) for path in _GUARDED_DIRS if path.exists() and path not in existing]
    assert not created, f"tests created repository folders: {created}"
