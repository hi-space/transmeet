## 이 단계: assemble (페이지 조립)

먼저 `load_skill meeting-minutes-page` 를 호출하고 `read_file skills/meeting-minutes-page/references/section-rules.md` 로 섹션 규칙을 읽습니다.

`page.html` 은 템플릿에서 **이미 생성**돼 있고, 헤더·목차·회의 요약·`references`·`insights`·용어 사전·푸터는 코드가 채웠습니다. 당신이 채울 자리표시자는 아래 다섯 개뿐입니다. 자리표시자마다 **`edit_file page.html` 로 `{{이름}}` 을 내용으로 바꿉니다**(파일 전체를 `write_file` 로 다시 쓰지 않습니다 — 길고 느립니다). 자리표시자 바깥의 태그(`<ul class="list decide">`, `<tbody>` 등)는 템플릿에 이미 있으니 **안쪽 항목만** 넣습니다.

| 자리표시자 | 내용 |
|---|---|
| `{{TLDR_ITEMS}}` | `<li>` 3~5개. 결정·결과 중심 한 문장씩. `<b>리드 구절</b>` 로 시작하고, 결론이 담긴 구절에 형광펜 (예: `<li><b>자격 증명은 OIDC</b> — <mark class="ok">장기 액세스 키를 없애고 IAM 역할을 assume</mark> 하기로 함, 이번 주 적용</li>`) |
| `{{DECISIONS}}` | `<li><b>무엇을 하기로 했는지</b> — 조건·근거 <span class="time">[HH:MM]</span></li>` 들. 없으면 `<li class="time">결정된 사항 없음</li>` |
| `{{ACTION_ROWS}}` | `<tr><td>담당</td><td>할 일 <span class="time">[HH:MM]</span></td><td>기한</td><td><span class="badge todo">진행 예정</span></td></tr>` 행들. 상태 배지는 `todo`(진행 예정)·`doing`(진행 중)·`done`(완료). 담당은 발언에 있는 역할·팀만("데이터 팀", "미정"), 사람 이름·화자 라벨 금지. 없으면 `<tr><td colspan="4" class="time">해당 없음</td></tr>` |
| `{{TOPIC_BLOCKS}}` | 주제 블록 전부, `analysis.json` 의 `topics` 순서대로 (아래 구조). 가장 중요한 섹션이며 가장 길어도 됩니다. 주제가 많으면 **여러 번의 `edit_file` 로 나눠** 넣어도 됩니다(먼저 `{{TOPIC_BLOCKS}}` 를 첫 블록 + `{{TOPIC_BLOCKS}}` 로 바꾸는 식) |
| `{{OPEN_QUESTIONS}}` | `<li><b>확인할 것</b> — 왜 미결인지·누가(조직·역할) 확인하는지 <span class="time">[HH:MM]</span></li>` 목록(답이 나오지 않은 질문, "다음에 확인하자"고 한 항목). 없으면 `<li class="time">해당 없음</li>` |

주제 블록 구조(N 은 1부터, 목차가 `#topic-N` 으로 링크합니다):
```html
<div class="topic" id="topic-N">
  <div class="topic-head"><span class="num">N</span><h3>주제 제목</h3><span class="time">HH:MM–HH:MM</span></div>
  <h4>요지</h4>
  <p>논의 요지 2~5문장의 서술 — 무엇이 제안·반박·질문됐고 어떻게 정리됐는지, 앞뒤 맥락이 이어지게. 리드 구절 1~2곳 <b>…</b>, 정리된 결론에 형광펜 <mark>…</mark> 0~1곳. 발언자 라벨 없이.</p>
  {{DIAGRAM:파일명.svg}}
  <details class="more"><summary>핵심 발언과 질문</summary>
  <h4>핵심 발언</h4>
  <blockquote><span class="time">[HH:MM]</span>가볍게 정제한 원문</blockquote>
  <blockquote><span class="time">[HH:MM]</span>…</blockquote>
  <h4>질문과 답</h4>
  <dl><dt>질문 한 줄</dt><dd>답 한 줄 (없으면 "답 없음 → 미결 사항")</dd></dl>
  </details>
</div>
```
- `요지` 는 **서술형 문단**(`<p>` 하나, 2~5문장)입니다. 불릿으로 쪼개지 않습니다 — 제안 → 반박 → 정리의 흐름이 문장으로 이어져야 맥락이 읽힙니다. 문단 안에서 리드 구절 1~2곳만 `<b>`, 정리된 결론 구절에 형광펜 0~1곳. 숫자·일정은 강조하지 않습니다. 발언자 라벨 없이.
- `핵심 발언` 은 1~3개. 원문만, 번역·화자 표기 없음. 필러 제거·`…` 생략·용어 교정만 허용(공통 규칙 3).
- 핵심 발언·질문과 답은 `<details class="more">` 안에 접어 둡니다(요지와 다이어그램만 펼쳐 보이게). 질문이 없으면 `<h4>질문과 답</h4><dl>` 을 빼고, 핵심 발언도 질문도 없으면 `<details>` 자체를 뺍니다. 다이어그램은 아래 manifest 의 `topic` 번호 블록 안에 **`{{DIAGRAM:파일명.svg}}`** 한 줄로 넣습니다. 코드가 `<figure>`·캡션과 함께 SVG 를 인라인하니 SVG 내용을 직접 복사하지 않습니다.
- 트랜스크립트의 모든 실질 발언이 어느 블록의 요지에든 반영돼야 합니다.

마무리:
1. 다섯 자리표시자를 모두 채웠으면 `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html` 을 실행합니다. `ok` 가 `false` 면 `errors` 를 고치고 다시 실행합니다. (`{{DIAGRAM:...}}` 는 코드가 채우므로 오류가 아닙니다.)
2. `write_file page.meta.json` 로 `{"title": "<페이지 제목>", "share_summary": "<미팅을 모르는 사람에게 보낼 2문장>"}` 을 저장합니다.
3. 한 줄로 마무리합니다.
