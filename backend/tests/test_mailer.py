"""mailer: 미팅 종료 메일 본문. 요약 실패·오디오 없음에도 메일은 쓸모 있어야 한다."""
from app import mailer

MEETING_URL = "https://transmeet.hi-yoo.com/?meeting=m1"


def build(**kw):
    args = dict(title="주간 1:1", started_at="2026-10-28T09:00:00+00:00", summary="## 결정\n- **출시** 11월",
                audio_url="https://s3.example/audio.mp3?sig", meeting_url=MEETING_URL)
    args.update(kw)
    return mailer.build_meeting_email(**args)


def test_email_includes_links():
    mail = build()
    assert mail["subject"] == "[TransMeet] 주간 1:1 요약"
    for body in (mail["html"], mail["text"]):
        assert "https://s3.example/audio.mp3?sig" in body
        assert MEETING_URL in body
        assert "미팅 화면의 &#x27;녹음 파일&#x27; 버튼" in body or "미팅 화면의 '녹음 파일' 버튼" in body
    assert "<strong>출시</strong>" in mail["html"]
    assert "<li>" in mail["html"]


def test_email_without_audio():
    mail = build(audio_url=None)
    assert "녹음 파일 다운로드" not in mail["html"]
    assert "녹음 파일 다운로드" not in mail["text"]
    assert MEETING_URL in mail["html"]


def test_email_summary_failed_text():
    mail = build(summary=None)
    assert mailer.SUMMARY_FAILED_TEXT in mail["html"]
    assert mailer.SUMMARY_FAILED_TEXT in mail["text"]


def test_email_escapes_html():
    mail = build(title="<script>x</script>", summary="- a < b & c")
    assert "<script>" not in mail["html"]
    assert "&lt;script&gt;" in mail["html"]
    assert "a &lt; b &amp; c" in mail["html"]
