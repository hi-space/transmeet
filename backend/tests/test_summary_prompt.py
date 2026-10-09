"""요약 패널용 트랜스크립트 포맷·프롬프트 조립 (app/summary_prompt.py)."""
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


async def test_summary_bedrock_client_waits_through_silent_thinking():
    """Opus 5.5 는 thinking 동안(표시 omitted) 스트림에 바이트가 오지 않는다. 기본 60초 읽기 타임아웃이면
    긴 트랜스크립트에서 SocketTimeoutError 로 끊기므로(2026-10-09 운영 로그) 요약 스트림은 긴 타임아웃을 쓴다."""
    from app import aws_clients

    assert aws_clients.SUMMARY_CLIENT_CONFIG.read_timeout >= 300
    async with aws_clients.bedrock_client(aws_clients.SUMMARY_CLIENT_CONFIG) as client:
        assert client.meta.config.read_timeout == aws_clients.SUMMARY_CLIENT_CONFIG.read_timeout
    async with aws_clients.bedrock_client() as client:  # 기본 호출은 그대로
        assert client.meta.config.read_timeout == 60
