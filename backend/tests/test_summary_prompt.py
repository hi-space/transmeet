"""요약 패널용 트랜스크립트 포맷·프롬프트 조립 (app/summary_prompt.py)."""
import pytest

from app.summary_prompt import SUMMARY_SYSTEM_PROMPT, build_summary_prompt, transcript_lines


def test_transcript_lines_drop_speaker_and_time():
    msgs = [
        {"speaker": "speaker1", "original": "파이프라인 어디까지 왔어요?", "timestamp": "2026-10-06T01:00:30+00:00"},
        {"speaker": "me", "original": "금요일까지 끝냅니다.", "timestamp": "2026-10-06T01:20:05Z"},
    ]
    assert transcript_lines(msgs) == ["파이프라인 어디까지 왔어요?", "금요일까지 끝냅니다."]


def test_transcript_lines_accept_dynamodb_field_names_and_skip_empty():
    msgs = [{"originalText": "첫 발언"}, {"originalText": "   "}, {"original": ""}]
    assert transcript_lines(msgs) == ["첫 발언"]


def test_prompt_has_page_structure_and_embeds_transcript():
    prompt = build_summary_prompt("주간 1:1", "[10:00] 파이프라인 어디까지 왔어요?")
    for heading in ["## 한눈에 보기", "## 회의 요약", "### 배경·목적", "### 주요 논의", "## 논의 흐름", "## 결정 사항",
                    "## 액션 아이템", "## 미결 사항"]:
        assert heading in prompt, heading
    assert "## 용어" not in prompt  # 요약 패널에는 용어 사전을 두지 않는다
    assert "## 기술 디테일" not in prompt  # 구체 정보는 해당 논의 불릿 안에
    assert "(HH:MM" not in prompt and "어디에도 쓰지 않습니다" in prompt  # 시각·시간 범위 금지
    assert "하위 불릿" in prompt  # 명사형이어도 근거·조건·수치는 하위 불릿으로 유지
    assert prompt.rstrip().endswith("[10:00] 파이프라인 어디까지 왔어요?")
    assert "주간 1:1" in prompt
    # 페이지와 같은 규칙: 화자 라벨 금지, 디테일 유지, 불릿당 강조 하나
    assert "화자" in prompt and "Speaker" in prompt
    assert "**" in prompt
    # 요약 패널은 훑는 용도: 발언 인용·시각 참조를 넣지 말라고 지시한다
    assert "인용하지 않습니다" in prompt and "`>` 인용 블록" in prompt
    assert "개조식 명사형" in prompt and "반복하지 않습니다" in prompt  # 간결한 명사형, 섹션 간 반복 금지
    assert "한국어" in SUMMARY_SYSTEM_PROMPT


def test_prompt_without_title_omits_title_line():
    assert "미팅 제목" not in build_summary_prompt("", "x")
    assert "미팅 제목: 주간 1:1" in build_summary_prompt("주간 1:1", "x")


@pytest.mark.parametrize(("style", "headings"), [
    ("report", ["## 요약", "## 배경", "## 결정 사항", "## 향후 일정"]),
    ("amazon", ["## 요약 (BLUF)", "## 고객 요구 (Customer Asks)", "## 주요 논의 (Discussion)", "## 결정 (Decisions)",
                "## 액션 아이템 (Action Items)", "## 리스크·미결 (Risks & Open Issues)"]),
    ("decisions", ["## 한 줄 요약", "## 결정 기록", "### D1.", "## 보류된 결정", "## 결정에 따른 할 일"]),
])
def test_selected_style_replaces_detailed_structure_and_keeps_source(style, headings):
    transcript = "데이터 팀이 금요일까지 검증합니다.\n출시 날짜는 아직 미정입니다."
    prompt = build_summary_prompt("출시 준비", transcript, style=style)
    assert prompt.endswith("\n---\n" + transcript)
    assert "미팅 제목: 출시 준비" in prompt
    for heading in headings:
        assert heading in prompt
    assert "## 논의 흐름" not in prompt
    assert "## 한눈에 보기" not in prompt  # 자세하게 구조가 섞이지 않는다


def test_amazon_style_forbids_guessed_owners_and_vague_words():
    prompt = build_summary_prompt("출시 준비", "원문", style="amazon")
    assert "추정 금지" in prompt and "모호어" in prompt and "Owner" in prompt


@pytest.mark.parametrize("style", ["amazon", "decisions"])
def test_new_record_styles_use_noun_form(style):
    assert "개조식 명사형" in build_summary_prompt("출시 준비", "원문", style=style)


def test_report_sections_follow_summary_background_decisions_schedule():
    prompt = build_summary_prompt("출시 준비", "원문", style="report")
    order = [prompt.index(h) for h in ["\n## 요약\n", "\n## 배경\n", "\n## 결정 사항\n", "\n## 향후 일정\n"]]
    assert order == sorted(order)
    assert "## 요청 드릴 사항" not in prompt


@pytest.mark.parametrize("style", [None, "", "unknown", "concise", "actions", "__proto__", 1, [], {}])
def test_unknown_styles_keep_legacy_detailed_behavior(style):
    assert build_summary_prompt("출시 준비", "원문", style=style) == build_summary_prompt("출시 준비", "원문")


def test_explicit_detailed_style_matches_legacy_call():
    assert build_summary_prompt("출시 준비", "원문", style="detailed") == build_summary_prompt("출시 준비", "원문")


async def test_summary_bedrock_client_waits_through_silent_thinking():
    """Opus 5.5 는 thinking 동안(표시 omitted) 스트림에 바이트가 오지 않는다. 기본 60초 읽기 타임아웃이면
    긴 트랜스크립트에서 SocketTimeoutError 로 끊기므로(2026-10-09 운영 로그) 요약 스트림은 긴 타임아웃을 쓴다."""
    from app import aws_clients

    assert aws_clients.SUMMARY_CLIENT_CONFIG.read_timeout >= 300
    async with aws_clients.bedrock_client(aws_clients.SUMMARY_CLIENT_CONFIG) as client:
        assert client.meta.config.read_timeout == aws_clients.SUMMARY_CLIENT_CONFIG.read_timeout
    async with aws_clients.bedrock_client() as client:  # 기본 호출은 그대로
        assert client.meta.config.read_timeout == 60


# ─── 내 양식(커스텀) ────────────────────────────────────────────────────────────

from app.summary_prompt import parse_summary_custom, resolve_summary_style  # noqa: E402

CUSTOM = {"templateId": "t1", "name": "주간 회의", "kind": "instruction",
          "body": "섹션은 이슈 / 원인 / 조치", "templateUpdatedAt": "2026-10-28T00:00:00Z"}


def test_custom_prompt_wraps_user_format_with_fixed_rules():
    p = build_summary_prompt("주간", "원문", style="custom", custom=CUSTOM)
    assert "<사용자_양식>\n섹션은 이슈 / 원인 / 조치\n</사용자_양식>" in p
    assert "트랜스크립트에서만" in p and "고정 규칙이 우선" in p
    assert p.endswith("\n---\n원문") and "미팅 제목: 주간" in p


def test_example_prompt_forbids_copying_example_content():
    p = build_summary_prompt("주간", "원문", style="custom",
                             custom={**CUSTOM, "kind": "example", "body": "## 이슈\n- A팀 3억"})
    assert "옮기지 않습니다" in p and "<사용자_양식>\n## 이슈\n- A팀 3억\n</사용자_양식>" in p
    assert "옮기지 않습니다" not in build_summary_prompt("주간", "원문", style="custom", custom=CUSTOM)


@pytest.mark.parametrize(("requested", "saved", "custom", "expected"), [
    ("custom", "detailed", CUSTOM, "custom"),
    (None, "custom", CUSTOM, "custom"),
    ("custom", "custom", None, "detailed"),
    ("report", "custom", CUSTOM, "report"),
    ("bogus", "custom", CUSTOM, "detailed"),
])
def test_resolve_summary_style(requested, saved, custom, expected):
    assert resolve_summary_style(requested, saved, custom) == expected


def test_builtin_prompts_unchanged_by_custom_argument():
    for s in ("detailed", "amazon", "decisions", "report"):
        assert build_summary_prompt("t", "x", s, custom=CUSTOM) == build_summary_prompt("t", "x", s)


def test_custom_without_snapshot_builds_detailed_prompt():
    assert build_summary_prompt("t", "x", "custom") == build_summary_prompt("t", "x", "detailed")


def _ddb_custom(kind, body):
    return {"meetingId": {"S": "m1"}, "templateId": {"S": "t1"}, "name": {"S": "n"}, "kind": {"S": kind},
            "body": {"S": body}, "templateUpdatedAt": {"S": "x"}}


def test_parse_summary_custom_rejects_bad_kind_and_oversize():
    assert parse_summary_custom(_ddb_custom("instruction", "a"))["body"] == "a"
    assert parse_summary_custom(_ddb_custom("example", "a" * 12000))["kind"] == "example"
    assert parse_summary_custom(_ddb_custom("other", "a")) is None
    assert parse_summary_custom(_ddb_custom("instruction", "a" * 6001)) is None
    assert parse_summary_custom(_ddb_custom("example", "a" * 12001)) is None
    assert parse_summary_custom(_ddb_custom("instruction", "   ")) is None
    assert parse_summary_custom(None) is None
