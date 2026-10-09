"""요약 패널용 트랜스크립트 포맷·프롬프트 조립 (app/summary_prompt.py)."""
from app.summary_prompt import SUMMARY_SYSTEM_PROMPT, build_summary_prompt, transcript_lines


def test_transcript_lines_use_kst_time_and_drop_speaker():
    msgs = [
        {"speaker": "speaker1", "original": "파이프라인 어디까지 왔어요?", "timestamp": "2026-10-06T01:00:30+00:00"},
        {"speaker": "me", "original": "금요일까지 끝냅니다.", "timestamp": "2026-10-06T01:20:05Z"},
    ]
    lines = transcript_lines(msgs)
    assert lines == ["[10:00] 파이프라인 어디까지 왔어요?", "[10:20] 금요일까지 끝냅니다."]
    assert "speaker1" not in "\n".join(lines) and "me" not in "".join(lines)


def test_transcript_lines_accept_dynamodb_field_names_and_skip_empty():
    msgs = [
        {"originalText": "첫 발언", "timestamp": "2026-10-06T01:00:00+00:00"},
        {"originalText": "   ", "timestamp": "2026-10-06T01:01:00+00:00"},
        {"original": "", "timestamp": "2026-10-06T01:02:00+00:00"},
    ]
    assert transcript_lines(msgs) == ["[10:00] 첫 발언"]


def test_transcript_lines_without_timestamp_still_keep_text():
    assert transcript_lines([{"original": "시각 없음"}, {"original": "깨진 시각", "timestamp": "not-a-date"}]) == ["시각 없음", "깨진 시각"]


def test_prompt_has_page_structure_and_embeds_transcript():
    prompt = build_summary_prompt("주간 1:1", "[10:00] 파이프라인 어디까지 왔어요?")
    for heading in ["## 한눈에 보기", "## 회의 요약", "### 배경·목적", "### 합의·결정", "## 논의 흐름", "## 결정 사항",
                    "## 액션 아이템", "## 미결 사항", "## 기술 디테일"]:
        assert heading in prompt, heading
    assert "## 용어" not in prompt  # 요약 패널에는 용어 사전을 두지 않는다
    assert prompt.rstrip().endswith("[10:00] 파이프라인 어디까지 왔어요?")
    assert "주간 1:1" in prompt
    # 페이지와 같은 규칙: 화자 라벨 금지, 디테일 유지, 불릿당 강조 하나
    assert "화자" in prompt and "Speaker" in prompt
    assert "**" in prompt
    # 요약 패널은 훑는 용도: 발언 인용·시각 참조를 넣지 말라고 지시한다
    assert "인용하지 않습니다" in prompt and "`>` 인용 블록" in prompt
    assert "서술형 문단으로 풀지 않습니다" in prompt
    assert "한국어" in SUMMARY_SYSTEM_PROMPT


def test_prompt_without_title_omits_title_line():
    assert "미팅 제목" not in build_summary_prompt("", "x")
    assert "미팅 제목: 주간 1:1" in build_summary_prompt("주간 1:1", "x")
