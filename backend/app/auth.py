"""WebSocket 연결의 사용자 확인과 미팅 소유권.

브라우저 WebSocket 은 헤더를 붙일 수 없어 Cognito ID 토큰을 연결 후 첫 메시지({action: "auth", token})로 받는다.
REST 는 API Gateway Cognito Authorizer 가 같은 ID 토큰을 검증하므로 두 경로의 사용자 ID(sub)가 같다.
"""
import asyncio
import logging
from functools import lru_cache
from typing import Optional

import jwt

from .aws_clients import dynamodb_client
from .config import config

logger = logging.getLogger(__name__)


class AuthError(Exception):
    pass


def _issuer() -> str:
    return f"https://cognito-idp.{config.REGION}.amazonaws.com/{config.COGNITO_USER_POOL_ID}"


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    # 서명 키는 프로세스 수명 동안 캐시한다. 모르는 kid 가 오면 PyJWKClient 가 다시 받아 온다.
    return jwt.PyJWKClient(f"{_issuer()}/.well-known/jwks.json", cache_keys=True)


def verify_id_token(token: str) -> str:
    """Cognito ID 토큰을 검증하고 사용자 ID(sub)를 돌려준다. 실패하면 AuthError."""
    if not config.COGNITO_USER_POOL_ID or not config.COGNITO_CLIENT_ID:
        raise AuthError("Cognito is not configured")
    if not token:
        raise AuthError("missing token")
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=config.COGNITO_CLIENT_ID,
            issuer=_issuer(),
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e
    if claims.get("token_use") != "id":
        raise AuthError("not an ID token")
    return claims["sub"]


async def verify_id_token_async(token: str) -> str:
    # JWKS 첫 조회는 동기 HTTP 라 이벤트 루프를 막지 않게 스레드에서 돌린다.
    return await asyncio.to_thread(verify_id_token, token)


async def owns_meeting(user_id: str, meeting_id: str) -> bool:
    """user_id 가 이 미팅의 소유자인지. userId 가 없는 항목은 누구의 것도 아니다."""
    if not user_id or not meeting_id:
        return False
    async with dynamodb_client() as ddb:
        result = await ddb.get_item(
            TableName=config.MEETINGS_TABLE,
            Key={"meetingId": {"S": meeting_id}},
            ProjectionExpression="userId",
        )
    owner: Optional[str] = result.get("Item", {}).get("userId", {}).get("S")
    return owner == user_id
