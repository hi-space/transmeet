# 섹션별 작성 규칙

모든 섹션은 존재해야 한다. 내용이 없으면 `<p class="time">해당 없음</p>`을 넣는다. 추측으로 채우지 않는다.

## header
입력 JSON의 `meeting` 값만 사용한다. 소요 시간은 `startedAt`과 마지막 메시지 `timestamp` 차이를 "1시간 12분" 형식으로 적는다. 참석자는 화자 라벨 그대로(Speaker 1, Speaker 2, Me). 실명을 지어내지 않는다.

## tldr
3~5개 `<li>`. 각 항목은 결정·결과·숫자 중심의 한 문장. "활발한 논의가 있었다" 같은 평가 문장은 쓰지 않는다.

## decisions
`<ul>`의 각 `<li>`는 "무엇을 하기로 했는지" + 근거 발언 시각 `<span class="time">[HH:MM]</span>`. 합의되지 않은 제안은 여기 넣지 않고 discussion 또는 open-questions에 둔다. 없으면 "결정된 사항 없음".

## action-items
표의 행마다 담당/할 일/기한/상태. 담당·기한이 발언에 없으면 "미정". 상태는 "진행 예정" / "진행 중" / "완료" 중 발언으로 확인되는 것만. 발언 시각을 할 일 칸 끝에 `[HH:MM]`으로 붙인다.

## discussion (가장 중요)
미팅의 시간 흐름을 그대로 따른다. 트랜스크립트를 처음부터 끝까지 읽고 주제가 바뀌는 지점마다 `.topic` 블록을 만든다. 블록당 구성:
1. `<div class="topic" id="topic-N">` (N은 1부터 순번) 안에 `<h3>` 주제명 + `<span class="time">HH:MM–HH:MM</span>`
2. 논의 요지 2~5문장 (누가 무엇을 제안·반박·질문했는지)
3. 핵심 발언 인용 1~3개: `<blockquote><span class="spk">Speaker 1 [HH:MM]</span> 한국어 번역<span class="orig">원문</span></blockquote>`
4. 이 블록에서 나온 질문과 답(있으면)
5. meeting-diagrams 스킬 기준에 맞는 경우에만 `<figure>` 다이어그램

규칙: 트랜스크립트의 모든 실질 발언이 어느 블록에든 반영되어야 한다. 인사·잡담·기술 문제("들리세요?")만 생략한다. 섹션 첫 요소는 주제 타임라인 다이어그램(meeting-diagrams의 `timeline_svg.py`).

## open-questions
답이 나오지 않은 질문, "다음에 확인하자"고 한 항목. 확인 담당이 언급되었으면 표기.

## references
meeting-research 스킬의 출력 포맷을 그대로 붙인다. 항목이 없으면 "리서치 대상이 되는 외부 사실이 없었습니다".

## insights
meeting-insights 스킬의 출력 포맷을 그대로 붙인다.

## glossary
미팅에 실제 등장한 영어 용어·약어만. 설명은 한 줄.

## footer
템플릿 그대로. `RESEARCH_DATE`는 오늘 날짜(YYYY-MM-DD).

## 금지
- `<script>`, `on*=` 속성, `javascript:` 링크, 외부 `src`/`@import`/`url(http...)`
- 외부 이미지. 모든 시각 요소는 인라인 SVG(`<title>` 필수)
- 허용 목록 밖의 태그. 쓸 수 있는 태그: html head body meta title style main header footer section article nav aside h1–h6 p div span ul ol li dl dt dd table thead tbody tfoot tr th td caption blockquote a b strong i em code pre br hr small sup sub details summary figure figcaption time abbr mark, SVG는 svg g path rect circle ellipse line polyline polygon text tspan title desc defs marker linearGradient stop clipPath (img·iframe·form·link·base·use·image·animate 등은 거절)
- 링크는 `https:`·`mailto:`·`#id`만. `http:`·`data:` 링크, `<meta http-equiv>`, SVG 안의 `<style>`, 인라인 `style="...url(...)"`, CSS 백슬래시 이스케이프, `url(#id)`가 아닌 `url()`, `<`·`>`가 들어간 주석
- 템플릿의 `{{...}}` 자리표시자를 남기는 것
