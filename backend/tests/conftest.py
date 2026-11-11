import pytest

from app import ws_handler


@pytest.fixture(autouse=True)
def instant_stream_restarts(monkeypatch):
    """실제 백오프(초 단위)를 기다리지 않는다 — 지연을 검증하는 테스트는 다시 덮어쓴다."""
    monkeypatch.setattr(ws_handler, "_stream_restart_delay", lambda attempt: 0, raising=False)
