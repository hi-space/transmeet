import pytest

from app import ws_handler


@pytest.fixture(autouse=True)
def instant_stream_restarts(monkeypatch):
    """실제 백오프(초 단위)를 기다리지 않는다 — 지연을 검증하는 테스트는 다시 덮어쓴다."""
    monkeypatch.setattr(ws_handler, "_stream_restart_delay", lambda attempt: 0, raising=False)


@pytest.fixture
def transcribe_sessions(monkeypatch):
    """폴백으로 열린 Transcribe 세션 — 실제 AWS 대신 큐가 닫힐 때까지 기다린다."""
    sessions = []

    async def run(state, ws, connection_id):
        sessions.append((state.stt_provider, state.source_lang, connection_id))
        while await state.audio_queue.get() is not None:
            pass

    monkeypatch.setattr(ws_handler, "_run_transcribe_streaming", run)
    return sessions
