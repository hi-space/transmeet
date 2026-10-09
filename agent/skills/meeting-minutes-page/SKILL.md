---
name: meeting-minutes-page
description: TransMeet 미팅 트랜스크립트로 공유용 단일 HTML 미팅록 페이지를 만들 때 반드시 사용한다. 페이지 구조(11개 섹션), 디자인 템플릿, 작성 규칙, 검증 스크립트, 발행 파일(page.html·page.meta.json) 규약을 정한다. "미팅 페이지", "미팅록 HTML", "회의록 페이지 생성", "페이지 조립" 요청이면 이 스킬을 먼저 읽는다. meeting-research, meeting-insights, meeting-diagrams 스킬과 함께 쓴다.
---

# Meeting Minutes Page

미팅 트랜스크립트를 읽고 **한 개의 self-contained HTML**(`page.html`)을 만든다. 저장된 파일을 시스템이 검증해 발행한다. 페이지는 미팅에 참석하지 않은 사람이 읽어도 흐름과 결론을 이해할 수 있어야 하고, 참석자가 읽으면 논의가 빠지지 않았다고 느껴야 한다. 페이지는 **발언자를 구분하지 않는다** — 누가 말했는지 대신 무엇이 논의됐는지를 쓴다.

## 입력

작업 디렉터리에 있다:
- `transcript.txt` — 발언 한 줄씩 `[HH:MM:SS] 원문` (시간순, 현지 시각). 음성 인식 결과라 용어가 틀리게 적힌 곳이 있다. 원본 JSON 은 `transcript.json`, `meeting.json`(`meetingId`, `title`, `startedAt`, `endedAt`, `generatedAt`, `modelId`).
- `analysis.json` — analyze 단계 산출물: `summary`(전체 서술 요약 문단), `terms`(정식 표기·녹취 표기·맥락 뜻·횟수), `topics`(start/end/title/summary), 리서치 대상, 다이어그램 계획.
- `research.html`, `insights.html` — meeting-research / meeting-insights 단계의 산출물(섹션 안에 들어갈 HTML 조각).
- `diagrams/*.svg`, `diagrams/manifest.json` — meeting-diagrams 단계의 산출물.

## 페이지 구조

목차(코드) · header(코드) · 1 한눈에 보기 · 2 회의 요약(코드, `analysis.summary`) · 3 결정 사항 · 4 액션 아이템 · 5 논의 흐름(타임라인은 코드, 주제 블록은 모델) · 6 미결 사항 · 7 관련 공식 자료(코드, `research.html`) · 8 인사이트 보고서(코드, `insights.html`) · 9 용어 사전(코드, `analysis.terms`) · footer(코드).

## 작업 순서

생성은 여러 단계(analyze → research ∥ diagrams → insights → assemble)로 나뉘어 돌고, 이 스킬은 마지막 **assemble** 단계의 규칙이다.

1. `transcript.txt`를 끝까지 읽는다. 주제 분할은 `analysis.json`의 `topics` 순서를 따른다 (`id="topic-1"`부터 — 목차가 이 앵커로 링크한다).
2. `references/section-rules.md`를 읽는다. 섹션별 규칙과 주제 블록 HTML 구조가 전부 거기 있다.
3. `page.html`은 `assets/template.html`에서 **이미 생성**돼 있고 코드가 채우는 부분은 채워져 있다. 남은 자리표시자 다섯 개(`{{TLDR_ITEMS}}`, `{{DECISIONS}}`, `{{ACTION_ROWS}}`, `{{TOPIC_BLOCKS}}`, `{{OPEN_QUESTIONS}}`)를 `edit_file`로 하나씩 내용으로 바꾼다. 파일 전체를 다시 쓰지 않는다. 바깥 태그(`<ol class="list">`, `<tbody>`)는 템플릿에 있으니 안쪽 항목만 넣는다.
4. `diagrams/manifest.json`의 flow/relations 다이어그램은 해당 주제 블록 안에 `{{DIAGRAM:파일명.svg}}` 한 줄로 넣는다. 시스템이 `<figure>`와 캡션을 붙여 SVG를 인라인한다.
5. `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html`을 실행한다. `ok`가 `false`면 `errors`를 고치고 다시 실행한다. 남은 `{{...}}` 자리표시자가 없어야 한다 (`{{DIAGRAM:...}}`, `{{STATS}}`는 예외 — 코드가 채운다).
6. `page.meta.json`에 `{"title": "<페이지 제목>", "share_summary": "<2문장>"}`을 저장한다. `share_summary`는 미팅을 모르는 사람에게 보낼 2문장.
7. 한 줄짜리 한국어 문장으로 마무리한다. 발행은 시스템이 한다.

작업 중 단계가 바뀔 때마다 한 줄짜리 한국어 상태 문장을 출력한다(예: "논의 흐름을 7개 주제로 나눴습니다"). 앱이 이 문장을 진행 로그로 보여준다.

## 작성 원칙

- 본문은 한국어. 인용은 **원문만**(번역·화자 없음). 가볍게 정제: 필러·더듬는 반복 제거, 생략은 `…`, 음성 인식 오류 용어는 `terms` 의 정식 표기로 교정. 뜻을 바꾸지 않는다.
- 기술 용어는 `analysis.json` 의 `terms` 정식 표기만 쓴다. 같은 것을 두 이름으로 부르지 않는다.
- 사실은 트랜스크립트와 검색 출처에서만. 추론은 insights 섹션에서만, "추정" 배지와 함께.
- 담당자·기한·수치를 지어내지 않는다. 없으면 "미정" 또는 "확인 불가". 담당은 역할·팀만, 사람 이름·화자 라벨 금지.
- 발언을 요약하되 **빠뜨리지 않는다**. 긴 미팅이면 주제 블록이 많아지는 것이 정상이다.
- 템플릿의 CSS 클래스(`.topic`, `.topic-head`, `.num`, `.time`, `.badge`, `.list`)와 변수(`--tm-*`)만 쓴다. 새 색상 코드·인라인 스타일을 넣지 않는다.

## 자주 하는 실수

- `discussion`을 요약 3줄로 끝내는 것 → 주제 블록마다 요지·핵심 발언·질문과 답을 채운다.
- 인용에 "Speaker 1 [10:07]" 라벨이나 번역 줄을 붙이는 것 → 시각 배지 + 원문만.
- 녹취의 잘못된 용어("MSCC", "블레이크")를 그대로 쓰는 것 → `terms` 의 정식 표기로.
- 검색으로 찾은 내용을 미팅 발언처럼 적는 것 → references/insights에만, 출처 링크와 함께.
- 외부 이미지·CDN 폰트 링크 → 전부 금지. 검증기가 거절한다.
- 검증 없이 끝내기 → 시스템이 같은 규칙으로 거절하고 오류 목록과 함께 조립 단계를 다시 시작한다(최대 2회). `validate_page.py`를 먼저 돌려라.
- `page.html` 전체를 `write_file`로 다시 쓰기 → 느리고 토큰을 낭비한다. 자리표시자마다 `edit_file`.
