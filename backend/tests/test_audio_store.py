"""audio_store: 연결별로 올린 PCM 구간을 시각순으로 이어 MP3 하나로 만든다."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import subprocess

import pytest

from app import audio_store
from app.config import config


class FakeBody:
    def __init__(self, data: bytes):
        self.data = data

    async def read(self):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeS3:
    def __init__(self):
        self.objects = {}  # key -> (bytes, LastModified)
        self.clock = datetime(2026, 10, 28, tzinfo=timezone.utc)

    def _tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock

    async def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = (bytes(Body), self._tick())

    async def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key][0])}

    async def head_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"LastModified": self.objects[Key][1]}

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                async def gen():
                    items = [
                        {"Key": k, "LastModified": v[1]}
                        for k, v in sorted(s3.objects.items(), key=lambda kv: kv[0][::-1])  # 일부러 섞는다
                        if k.startswith(Prefix)
                    ]
                    yield {"Contents": items}
                return gen()
        return P()


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()

    @asynccontextmanager
    async def fake_client():
        yield fake

    monkeypatch.setattr(audio_store, "s3_client", fake_client)
    monkeypatch.setattr(config, "AUDIO_BUCKET", "bucket")
    audio_store._merge_locks.clear()
    return fake


def duration(mp3: bytes, tmp_path) -> float:
    path = tmp_path / "out.mp3"
    path.write_bytes(mp3)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, check=True,
    )
    return float(out.stdout)


def tone(seconds: float) -> bytes:
    return b"\x00\x10" * int(16000 * seconds)


def test_part_key_sorts_by_time():
    t1 = datetime(2026, 10, 28, 9, 5, 3, 120000, tzinfo=timezone.utc)
    t2 = t1 + timedelta(milliseconds=1)
    k1 = audio_store.part_key("m1", "zzzzzzzz-1", t1)
    k2 = audio_store.part_key("m1", "aaaaaaaa-2", t2)
    assert k1.startswith("meetings/m1/parts/") and k1.endswith("-zzzzzzzz.pcm")
    assert k1 < k2


async def test_upload_part_skips_empty(s3):
    assert await audio_store.upload_part("m1", "conn", b"") is None
    assert s3.objects == {}


async def test_merge_concatenates_in_key_order(s3, tmp_path):
    await audio_store.upload_part("m1", "c1", tone(0.5))
    await audio_store.upload_part("m1", "c2", tone(0.5))
    await audio_store.upload_part("other", "c3", tone(0.5))
    key = await audio_store.ensure_merged("m1")
    assert key == "meetings/m1/audio.mp3"
    data = s3.objects[key][0]
    assert data[:3] == b"ID3" or data[0] == 0xFF
    # m1 의 두 구간(0.5초 × 2)만 합쳐졌다 — other 미팅 구간은 섞이지 않는다
    assert duration(data, tmp_path) == pytest.approx(1.0, abs=0.1)


async def test_merge_reuses_fresh_mp3(s3):
    await audio_store.upload_part("m1", "c1", tone(0.2))
    await audio_store.ensure_merged("m1")
    first = s3.objects["meetings/m1/audio.mp3"][1]
    await audio_store.ensure_merged("m1")
    assert s3.objects["meetings/m1/audio.mp3"][1] == first
    await audio_store.upload_part("m1", "c1", tone(0.2))
    await audio_store.ensure_merged("m1")
    assert s3.objects["meetings/m1/audio.mp3"][1] > first


async def test_merge_no_parts_returns_none(s3):
    assert await audio_store.ensure_merged("m1") is None
