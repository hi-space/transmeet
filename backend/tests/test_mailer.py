"""mailer: 미팅 종료 메일 본문. 요약 실패·오디오 없음에도 메일은 쓸모 있어야 한다."""
from xml.etree import ElementTree

import pytest

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


def rendered_table(markdown):
    root = ElementTree.fromstring(f"<main>{mailer.summary_to_html(markdown)}</main>")
    table = root.find("table")
    assert table is not None, "Markdown tables must become a real HTML table"
    rows = [["".join(cell.itertext()) for cell in row] for row in table.findall(".//tr")]
    return table, rows


def test_summary_renders_table_headers_rows_alignment_and_inline_formatting():
    table, rows = rendered_table(
        "| 할 일 | 담당자 | 기한 |\n"
        "| :--- | :---: | ---: |\n"
        "| **로그인 수정** | 민수 | 금요일 |\n"
        "| 배포 확인 | 지수 | 다음 주 |\n"
    )
    assert rows == [
        ["할 일", "담당자", "기한"],
        ["로그인 수정", "민수", "금요일"],
        ["배포 확인", "지수", "다음 주"],
    ]
    headers = table.findall("./thead/tr/th")
    assert len(headers) == 3
    assert all(header.get("scope") == "col" for header in headers)
    assert "text-align:left" in headers[0].get("style", "")
    assert "text-align:center" in headers[1].get("style", "")
    assert "text-align:right" in headers[2].get("style", "")
    assert table.find("./tbody/tr/td/strong").text == "로그인 수정"
    assert "border-collapse:collapse" in table.get("style", "")


@pytest.mark.parametrize(
    "markdown, expected",
    [
        ("항목 | 상태\n--- | ---\n출시 | 완료", [["항목", "상태"], ["출시", "완료"]]),
        ("| 이름 |\n| --- |\n| 회의 |", [["이름"], ["회의"]]),
        (r"| 조건 | 결과 |" "\n| --- | --- |\n" r"| A \| B | 완료 |",
         [["조건", "결과"], ["A | B", "완료"]]),
        ("| 하나 | 둘 |\n| --- | --- |\n| 값 |\n| A | B | 무시 |",
         [["하나", "둘"], ["값", ""], ["A", "B"]]),
        ("| 하나 | 둘 |\n| - | -- |\n값",
         [["하나", "둘"], ["값", ""]]),
        ("항목\n| --- |\n완료", [["항목"], ["완료"]]),
    ],
)
def test_summary_renders_common_table_variants(markdown, expected):
    _, rows = rendered_table(markdown)
    assert rows == expected


def test_table_cells_escape_untrusted_html():
    table, rows = rendered_table(
        '| 이름 | 내용 |\n| --- | --- |\n'
        '| <img src=x onerror="alert(1)"> | a < b & c |'
    )
    assert rows[1] == ['<img src=x onerror="alert(1)">', "a < b & c"]
    assert table.find(".//img") is None


def test_table_boundaries_preserve_lists_headings_and_plain_pipe_text():
    rendered = mailer.summary_to_html(
        "- 첫 결정\n\n"
        "| 항목 | 상태 |\n| --- | --- |\n| 출시 | 완료 |\n\n"
        "## 다음 단계\n- 결과 공유\n\n"
        "A | B\n구분선이 아닌 문장\n"
    )
    root = ElementTree.fromstring(f"<main>{rendered}</main>")
    assert [child.tag for child in root] == ["ul", "table", "h3", "ul", "p", "p"]
    assert root.findall("p")[0].text == "A | B"


def test_mismatched_table_header_and_separator_are_not_misread_as_a_table():
    rendered = mailer.summary_to_html("| 하나 | 둘 |\n| --- |\n| 값 | 값 |")
    assert "<table" not in rendered
    assert "| 하나 | 둘 |" in rendered


def test_table_stops_before_a_new_markdown_block_even_when_it_contains_pipes():
    rendered = mailer.summary_to_html(
        "| 항목 | 상태 |\n| --- | --- |\n| 출시 | 완료 |\n"
        "## 다음 단계 | 참고\n- 결과 | 공유"
    )
    root = ElementTree.fromstring(f"<main>{rendered}</main>")
    assert [child.tag for child in root] == ["table", "h3", "ul"]
    assert root.find("h3").text == "다음 단계 | 참고"


def test_plain_setext_heading_is_not_misread_as_a_single_column_table():
    assert "<table" not in mailer.summary_to_html("제목\n---")


def test_email_includes_a_published_page_in_both_formats():
    url = "https://transmeet.hi-yoo.com/p/m1/page1.html"
    mail = build(page_url=url)
    assert f'href="{url}"' in mail["html"]
    assert "회의 페이지 보기" in mail["html"]
    assert f"회의 페이지 보기: {url}" in mail["text"]
    assert MEETING_URL in mail["html"]
    assert MEETING_URL in mail["text"]


def test_email_omits_page_button_when_no_page_is_published():
    mail = build()
    assert "회의 페이지 보기" not in mail["html"]
    assert "회의 페이지 보기" not in mail["text"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,test", "/p/m1/page1.html"])
def test_email_does_not_turn_an_invalid_page_url_into_a_link(url):
    mail = build(page_url=url)
    assert "회의 페이지 보기" not in mail["html"]
    assert "회의 페이지 보기" not in mail["text"]
