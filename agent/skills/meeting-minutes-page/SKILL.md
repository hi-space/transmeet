---
name: meeting-minutes-page
description: TransMeet 미팅 트랜스크립트(JSON)로 공유용 단일 HTML 미팅록 페이지를 만들 때 반드시 사용한다. 페이지 구조(10개 섹션), 디자인 템플릿, 작성 규칙, 검증 스크립트, publish_page 호출 순서를 정한다. "미팅 페이지", "미팅록 HTML", "회의록 페이지 생성", "publish_page" 요청이면 이 스킬을 먼저 읽는다. meeting-research, meeting-insights, meeting-diagrams 스킬과 함께 쓴다.
---

# Meeting Minutes Page

미팅 트랜스크립트를 읽고 **한 개의 self-contained HTML**을 만들어 `publish_page`로 발행한다. 페이지는 미팅에 참석하지 않은 사람이 읽어도 흐름과 결론을 이해할 수 있어야 하고, 참석자가 읽으면 자기 발언이 빠지지 않았다고 느껴야 한다.

## 입력

사용자 메시지에 `meeting` JSON이 온다:
- `meeting`: `meetingId`, `title`, `startedAt`, `endedAt`, `speakers`, `languages`, `generatedAt`, `modelId`
- `messages[]`: `timestamp`, `speaker`, `original`, `translation`, `detectedLanguage` (시간순)

## 작업 순서

1. 트랜스크립트를 끝까지 읽고 주제 전환 지점을 메모한다 (시각, 주제명).
2. `references/section-rules.md`를 읽는다. 섹션별 규칙이 전부 거기 있다.
3. **meeting-research** 스킬을 로드해 리서치 대상을 뽑고 Web Search로 공식 자료를 찾아 더블 체크한다 (최대 12회 검색).
4. **meeting-insights** 스킬을 로드해 인사이트 보고서를 쓴다.
5. **meeting-diagrams** 스킬을 로드해 주제 타임라인과(필요한 경우) 논의 다이어그램 SVG를 만든다.
6. `assets/template.html`을 작업 디렉터리에 복사하고 `{{...}}` 자리표시자를 모두 채운다. 남은 자리표시자가 없어야 한다.
7. `python3 scripts/validate_page.py page.html`을 실행한다. `ok`가 `false`면 `errors`를 고치고 다시 실행한다. shell이 없으면 code_interpreter에서 스크립트 내용을 읽어 실행한다.
8. 검증을 통과한 HTML 전체를 `publish_page(title, html, share_summary)`로 **정확히 한 번** 호출한다. `share_summary`는 미팅을 모르는 사람에게 보낼 2문장.
9. 반환된 URL을 받으면 두 문장 이내 한국어로 마무리한다.

작업 중 단계가 바뀔 때마다 한 줄짜리 한국어 상태 문장을 출력한다(예: "논의 흐름을 7개 주제로 나눴습니다"). 앱이 이 문장을 진행 로그로 보여준다.

## 작성 원칙

- 본문은 한국어. 인용은 번역 + 원문 병기.
- 사실은 트랜스크립트와 검색 출처에서만. 추론은 insights 섹션에서만, "추정" 배지와 함께.
- 담당자·기한·수치를 지어내지 않는다. 없으면 "미정" 또는 "확인 불가".
- 발언을 요약하되 **빠뜨리지 않는다**. 긴 미팅이면 주제 블록이 많아지는 것이 정상이다.
- 템플릿의 CSS 변수(`--tm-*`)만 쓴다. 새 색상 코드를 직접 넣지 않는다.

## 자주 하는 실수

- `discussion`을 요약 3줄로 끝내는 것 → 주제 블록마다 요지·인용·질답을 채운다.
- 검색으로 찾은 내용을 미팅 발언처럼 적는 것 → references/insights에만, 출처 링크와 함께.
- 외부 이미지·CDN 폰트 링크 → 전부 금지. 검증기가 거절한다.
- `publish_page`를 검증 전에 호출 → 백엔드가 같은 규칙으로 거절하고 사유를 돌려준다. 그러면 고쳐서 다시 호출한다(최대 2회).
