## 이 단계: assemble (페이지 조립)

먼저 `load_skill meeting-minutes-page` 를 호출하고 `read_file skills/meeting-minutes-page/references/section-rules.md` 로 섹션 규칙을 읽습니다.

`page.html` 은 템플릿에서 **이미 생성**돼 있고, 헤더(제목·일시·참석자)·푸터·`references`·`insights`·주제 타임라인은 코드가 채웠습니다. 당신이 채울 자리표시자는 아래 목록뿐입니다. 자리표시자마다 **`edit_file page.html` 로 `{{이름}}` 을 내용으로 바꿉니다**(파일 전체를 `write_file` 로 다시 쓰지 않습니다 — 길고 느립니다).

| 자리표시자 | 내용 |
|---|---|
| `{{TLDR_ITEMS}}` | `<li>` 3~5개 |
| `{{DECISIONS}}` | `<li>` 목록 (`<span class="time">[HH:MM]</span>` 포함). 없으면 `<li>결정된 사항 없음</li>` |
| `{{ACTION_ROWS}}` | `<tr><td>담당</td><td>할 일 [HH:MM]</td><td>기한</td><td>상태</td></tr>` 행들. 없으면 `<tr><td colspan="4" class="time">해당 없음</td></tr>` |
| `{{TOPIC_BLOCKS}}` | 주제 블록 전부. `outline.json` 의 `topics` 순서대로 `<div class="topic" id="topic-N">` … (section-rules 의 discussion 규칙). 가장 중요한 섹션이며 가장 길어도 됩니다. 주제가 많으면 **여러 번의 `edit_file` 로 나눠** 넣어도 됩니다(먼저 `{{TOPIC_BLOCKS}}` 를 첫 블록 + `{{TOPIC_BLOCKS}}` 로 바꾸는 식) |
| `{{OPEN_QUESTIONS}}` | `<li>` 목록. 없으면 `<li class="time">해당 없음</li>` |
| `{{GLOSSARY_ROWS}}` | `<tr><td>용어</td><td>한 줄 설명</td></tr>`. 없으면 `<tr><td colspan="2" class="time">해당 없음</td></tr>` |

다이어그램: 아래 manifest 에 있는 `flow`/`relations` SVG 는 해당 주제 블록 안에 **`{{DIAGRAM:파일명.svg}}`** 한 줄로 넣습니다(예: `{{DIAGRAM:decision-flow.svg}}`). 코드가 `<figure>`·캡션과 함께 SVG 를 인라인합니다. SVG 내용을 직접 복사하지 않습니다.

마무리:
1. 모든 자리표시자를 채웠으면 `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html` 을 실행합니다. `ok` 가 `false` 면 `errors` 를 고치고 다시 실행합니다. (`{{DIAGRAM:...}}` 마커는 코드가 바꾸므로 오류가 아닙니다.)
2. `write_file page.meta.json` 로 `{"title": "<페이지 제목>", "share_summary": "<미팅을 모르는 사람에게 보낼 2문장>"}` 을 저장합니다.
3. 한 줄로 마무리합니다.
