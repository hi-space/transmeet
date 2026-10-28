"""WebSocket 사용자 격리: ID 토큰 검증과 미팅 소유권.

REST 는 API Gateway Cognito Authorizer 가 막지만 WebSocket 은 ALB 로 바로 들어와
백엔드가 직접 토큰을 검증하고, meetingId 가 상태에 들어가는 모든 곳에서 소유자를 확인한다.
"""
import time
from contextlib import asynccontextmanager

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, ws_handler
from app.config import config
from app.ws_handler import ConnectionState

POOL_ID = "us-east-1_TESTPOOL"
CLIENT_ID = "test-client"
SUB = "user-a"


@pytest.fixture(autouse=True)
def cognito_config(monkeypatch):
    monkeypatch.setattr(config, "REGION", "us-east-1")
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", POOL_ID)
    monkeypatch.setattr(config, "COGNITO_CLIENT_ID", CLIENT_ID)


@pytest.fixture
def signing_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, _token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(auth, "_jwks_client", lambda: FakeJwks())
    return key


def make_token(key, **overrides):
    claims = {
        "sub": SUB,
        "aud": CLIENT_ID,
        "iss": f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}",
        "token_use": "id",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


# ─── verify_id_token ─────────────────────────────────────────────────────────

def test_valid_id_token_returns_sub(signing_key):
    assert auth.verify_id_token(make_token(signing_key)) == SUB


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "other-client"},
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/other-pool"},
        {"token_use": "access"},
        {"exp": int(time.time()) - 10},
    ],
    ids=["wrong-audience", "wrong-issuer", "access-token", "expired"],
)
def test_rejects_bad_claims(signing_key, overrides):
    with pytest.raises(auth.AuthError):
        auth.verify_id_token(make_token(signing_key, **overrides))


def test_rejects_token_signed_by_other_key(signing_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(auth.AuthError):
        auth.verify_id_token(make_token(other))


def test_rejects_when_cognito_not_configured(signing_key, monkeypatch):
    # 설정이 빠진 배포가 모든 연결을 통과시키지 않게 — 닫힌 쪽으로 실패한다
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "")
    with pytest.raises(auth.AuthError):
        auth.verify_id_token(make_token(signing_key))


# ─── owns_meeting ────────────────────────────────────────────────────────────

class FakeDdb:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    async def get_item(self, Key, **_kwargs):
        self.calls += 1
        owner = self.items.get(Key["meetingId"]["S"])
        return {"Item": {"userId": {"S": owner}}} if owner else {"Item": {}}


def patch_ddb(monkeypatch, ddb):
    @asynccontextmanager
    async def fake_client():
        yield ddb

    monkeypatch.setattr(auth, "dynamodb_client", fake_client)


async def test_owns_meeting(monkeypatch):
    patch_ddb(monkeypatch, FakeDdb({"m-mine": SUB, "m-theirs": "user-b"}))
    assert await auth.owns_meeting(SUB, "m-mine") is True
    assert await auth.owns_meeting(SUB, "m-theirs") is False
    # userId 가 없는 항목(마이그레이션 전 데이터)은 누구의 것도 아니다
    assert await auth.owns_meeting(SUB, "m-legacy") is False


# ─── _claim_meeting ──────────────────────────────────────────────────────────

async def test_claim_meeting_caches_owned_and_rejects_others(monkeypatch):
    ddb = FakeDdb({"m-mine": SUB, "m-theirs": "user-b"})
    patch_ddb(monkeypatch, ddb)
    state = ConnectionState(ws=None, user_id=SUB)

    assert await ws_handler._claim_meeting(state, "m-mine") == "m-mine"
    assert await ws_handler._claim_meeting(state, "m-mine") == "m-mine"
    assert ddb.calls == 1  # 두 번째는 캐시

    assert await ws_handler._claim_meeting(state, "m-theirs") is None
    assert await ws_handler._claim_meeting(state, None) is None


# ─── ws_endpoint ─────────────────────────────────────────────────────────────

def _client():
    app = FastAPI()
    app.include_router(ws_handler.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "first",
    [{"action": "ping"}, {"action": "auth"}, {"action": "auth", "token": "garbage"}],
    ids=["not-auth", "no-token", "bad-token"],
)
def test_ws_without_valid_auth_is_closed_4401(signing_key, first):
    with _client().websocket_connect("/ws?meetingId=m-mine") as ws:
        ws.send_json(first)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == ws_handler.WS_CLOSE_UNAUTHORIZED


def test_ws_with_valid_token_is_accepted(signing_key, monkeypatch):
    patch_ddb(monkeypatch, FakeDdb({"m-mine": SUB}))
    with _client().websocket_connect("/ws?meetingId=m-mine") as ws:
        ws.send_json({"action": "auth", "token": make_token(signing_key)})
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"
