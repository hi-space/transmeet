"""녹음 오디오 보관.

WS 로 들어오는 16 kHz mono s16le PCM 을 연결별로 모아 S3 에 구간(part) 객체로 올리고,
다운로드·미팅 종료 때 구간을 키 순서(= 시각순)로 이어 ffmpeg 로 MP3 하나를 만든다.

구간으로 나누는 이유: 연결이 끊기거나 태스크가 교체돼도 그때까지 올린 구간은 남는다.
키에 시각과 연결 ID 를 넣어 여러 연결·태스크가 같은 미팅에 올려도 겹치지 않는다.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from botocore.exceptions import ClientError

from .aws_clients import s3_client
from .config import config

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MP3_BITRATE = "64k"

# 같은 미팅의 합치기가 겹치면 같은 키에 두 번 쓰고 두 번 변환한다 — 프로세스 안에서만 직렬화한다.
_merge_locks: Dict[str, Tuple[asyncio.Lock, int]] = {}


def _prefix(meeting_id: str) -> str:
    return f"meetings/{meeting_id}/"


def mp3_key(meeting_id: str) -> str:
    return f"{_prefix(meeting_id)}audio.mp3"


def part_key(meeting_id: str, conn_id: str, now: datetime) -> str:
    """시각(마이크로초까지)이 앞에 와서 사전순 = 시간순이다."""
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{_prefix(meeting_id)}parts/{stamp}-{conn_id[:8]}.pcm"


async def upload_part(
    meeting_id: str, conn_id: str, pcm: bytes, started_at: Optional[datetime] = None
) -> Optional[str]:
    """`started_at` 은 이 구간의 첫 조각을 받은 시각. 올린 시각으로 키를 매기면, 끊긴 연결의 구간이
    ping 타임아웃 뒤 늦게 올라가 재접속 후 구간보다 뒤에 정렬된다."""
    if not pcm or not config.AUDIO_BUCKET:
        return None
    key = part_key(meeting_id, conn_id, started_at or datetime.now(timezone.utc))
    async with s3_client() as s3:
        await s3.put_object(Bucket=config.AUDIO_BUCKET, Key=key, Body=pcm, ContentType="audio/L16")
    return key


async def _list_parts(s3, meeting_id: str) -> List[dict]:
    parts: List[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=config.AUDIO_BUCKET, Prefix=f"{_prefix(meeting_id)}parts/"):
        parts.extend(page.get("Contents", []))
    return sorted(parts, key=lambda o: o["Key"])


async def _mp3_modified(s3, meeting_id: str) -> Optional[datetime]:
    try:
        head = await s3.head_object(Bucket=config.AUDIO_BUCKET, Key=mp3_key(meeting_id))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return head["LastModified"]


async def _encode_parts(s3, keys: List[str]) -> bytes:
    """구간을 하나씩 ffmpeg stdin 에 흘려 넣는다 — 1시간 PCM(약 115 MB)을 메모리에 다 올리지 않는다."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        "-b:a", MP3_BITRATE, "-f", "mp3", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    stdout_task = asyncio.create_task(proc.stdout.read())
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        for key in keys:
            obj = await s3.get_object(Bucket=config.AUDIO_BUCKET, Key=key)
            async with obj["Body"] as body:
                proc.stdin.write(await body.read())
            await proc.stdin.drain()
        proc.stdin.close()
        out, err = await stdout_task, await stderr_task
        if await proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:500]}")
        return out
    finally:
        # 중간에 실패해도(S3 오류, ffmpeg 조기 종료) 좀비 프로세스와 회수 안 된 태스크를 남기지 않는다
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        for t in (stdout_task, stderr_task):
            if not t.done():
                t.cancel()
            await asyncio.gather(t, return_exceptions=True)


async def ensure_merged(meeting_id: str) -> Optional[str]:
    """최신 MP3 의 키를 돌려준다. 구간이 없으면 None.

    MP3 가 가장 늦은 구간보다 나중에 만들어졌으면 다시 변환하지 않는다.
    """
    if not config.AUDIO_BUCKET:
        return None
    # 미팅마다 락이 쌓이지 않게 쓰는 쪽 수를 센다. Lock.locked() 는 해제 직후 대기자가 있어도 False 라 기준이 못 된다.
    lock, users = _merge_locks.get(meeting_id, (asyncio.Lock(), 0))
    _merge_locks[meeting_id] = (lock, users + 1)
    try:
        async with lock:
            return await _merge_locked(meeting_id)
    finally:
        lock, users = _merge_locks[meeting_id]
        if users <= 1:
            _merge_locks.pop(meeting_id, None)
        else:
            _merge_locks[meeting_id] = (lock, users - 1)


async def _merge_locked(meeting_id: str) -> Optional[str]:
    async with s3_client() as s3:
        parts = await _list_parts(s3, meeting_id)
        if not parts:
            return None
        newest_part = max(p["LastModified"] for p in parts)
        mp3_at = await _mp3_modified(s3, meeting_id)
        if mp3_at is not None and mp3_at >= newest_part:
            return mp3_key(meeting_id)
        data = await _encode_parts(s3, [p["Key"] for p in parts])
        await s3.put_object(
            Bucket=config.AUDIO_BUCKET, Key=mp3_key(meeting_id), Body=data, ContentType="audio/mpeg"
        )
        logger.info("[audio] merged meeting_id=%s parts=%d mp3_bytes=%d", meeting_id, len(parts), len(data))
        return mp3_key(meeting_id)


async def presign(key: str, expires: int, filename: str) -> str:
    # filename* 로 한글 미팅 제목도 그대로 파일 이름이 된다 (RFC 5987)
    disposition = f"attachment; filename=\"meeting.mp3\"; filename*=UTF-8''{quote(filename)}"
    async with s3_client() as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.AUDIO_BUCKET, "Key": key, "ResponseContentDisposition": disposition},
            ExpiresIn=expires,
        )
