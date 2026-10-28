# 섹션별 작성 규칙

모든 섹션은 존재해야 한다. 내용이 없으면 `<li class="time">해당 없음</li>`(목록) 또는 `<tr><td colspan="N" class="time">해당 없음</td></tr>`(표)을 넣는다. 추측으로 채우지 않는다.

읽는 사람이 **핵심을 먼저 잡고 디테일은 이어 읽는** 구조로 쓴다. 목록 섹션(tldr·summary·decisions·open-questions)은 불릿 하나에 사실 하나, 항목마다 리드 구절 하나를 `<b>` 로 감싸 앞쪽에 둔다. 논의 흐름(discussion 의 요지)은 맥락이 이어지는 서술형 문단으로 쓰고 문단 안에서 `<b>` 1~2곳만. **형광펜 `<mark>` 은 결론 구절에만**(합의·확정 `<mark class="ok">`, 리스크·미확인 `<mark class="risk">`), 항목마다 0~1곳·전체 항목의 절반 이하. **숫자·일정·기한은 강조하지 않는다.** 문장 전체를 굵게 하지 않고, 조건·근거·수치를 빼지 않는다.

이 문서는 `detailed` 스타일의 섹션 규칙이다. `visual` 스타일은 `runtime/visual.py` 와 `runtime/prompts/page_*.md` 가 구조를 정한다.

페이지는 **발언자를 구분하지 않는다**. "Speaker 1", "Me", "고객 측", "AWS 측" 같은 라벨을 어디에도 쓰지 않고, 누가 말했는지 대신 무엇이 논의됐는지를 쓴다. 기술 용어는 `analysis.json` 의 `terms` 에 있는 정식 표기만 쓴다.

## header (코드가 채움)
`meeting.json` 값으로 제목·일시·소요 시간을 코드가 채운다. 모델은 건드리지 않는다.

## 목차 (코드가 채움)
섹션 9개와 `topics` 의 주제 목록을 `#sec-*`, `#topic-N` 앵커로 코드가 만든다. 그래서 주제 블록 `id` 는 반드시 `topic-1`, `topic-2`… 순번이어야 한다.

## tldr
3~5개 `<li>`. 각 항목은 결정·결과 중심의 한 문장으로, `<b>리드 구절</b>` 로 시작하고 결론 구절에 형광펜 0~1곳. "활발한 논의가 있었다" 같은 평가 문장은 쓰지 않는다.

## summary (코드가 채움)
analyze 단계의 `summary` 를 소제목 그룹(`<h4>` 배경·목적 / 주요 논의 / 합의·결정 / 남은 일)과 `<ul class="points">` 불릿으로 넣는다. 불릿 안의 `<b>`·`<mark>`(`class="ok|risk"` 포함) 만 살리고 다른 마크업은 글자로 바꾼다.

## decisions
`<li>` 마다 `<b>무엇을 하기로 했는지</b>` + 조건·근거 + 발언 시각 `<span class="time">[HH:MM]</span>`. 바깥 `<ul class="list decide">` 는 템플릿에 있다. 합의되지 않은 제안은 여기 넣지 않고 discussion 또는 open-questions에 둔다. 없으면 `<li class="time">결정된 사항 없음</li>`.

## action-items
표의 행마다 담당/할 일/기한/상태. 담당은 발언에 나온 역할·팀·조직만("데이터 팀", "미정") — 사람 이름·화자 라벨은 쓰지 않는다. 기한이 발언에 없으면 "미정". 상태는 발언으로 확인되는 것만 배지로: `<span class="badge todo">진행 예정</span>` / `<span class="badge doing">진행 중</span>` / `<span class="badge done">완료</span>`. 발언 시각을 할 일 칸 끝에 `<span class="time">[HH:MM]</span>` 으로 붙인다.

## discussion (가장 중요)
미팅의 시간 흐름을 그대로 따른다. `analysis.json` 의 `topics` 순서대로 `.topic` 블록:

```html
<div class="topic" id="topic-N">
  <div class="topic-head"><span class="num">N</span><h3>주제 제목</h3><span class="time">HH:MM–HH:MM</span></div>
  <h4>요지</h4>
  <p>2~5문장 서술. 무엇이 제안·반박·질문됐고 어떻게 정리됐는지. 리드 구절 1~2곳 <b>…</b>, 결론에 <mark>형광펜</mark> 0~1곳.</p>
  {{DIAGRAM:파일명.svg}}
  <details class="more"><summary>핵심 발언과 질문</summary>
  <h4>핵심 발언</h4>
  <blockquote><span class="time">[HH:MM]</span>원문</blockquote>
  <h4>질문과 답</h4>
  <dl><dt>질문</dt><dd>답</dd></dl>
  </details>
</div>
```

- 요지는 **서술형 문단** `<p>` 하나(2~5문장). 불릿으로 쪼개지 않는다 — 제안 → 반박 → 정리의 흐름이 이어져야 맥락이 읽힌다. 문단 안에서 `<b>` 1~2곳, 결론에 형광펜 0~1곳. 숫자는 강조하지 않는다.
- 핵심 발언 1~3개. **원문만** — 번역·화자 없음. 가볍게 정제: 필러("뭐", "이제", "그게", "어…")와 더듬는 반복을 지우고 생략은 `…`, 음성 인식 오류로 틀린 용어는 정식 표기로 바로잡는다. 뜻을 바꾸거나 말을 보태지 않는다.
- 질문과 답은 그 주제에 질문이 있었을 때만. 답이 없으면 `<dd>답 없음 → 미결 사항</dd>`.
- 다이어그램은 `diagrams/manifest.json` 의 `topic` 번호 블록에 `{{DIAGRAM:파일명.svg}}` 한 줄. 코드가 `<figure>` 로 바꾼다.
- 트랜스크립트의 모든 실질 발언이 어느 블록의 요지에든 반영되어야 한다. 인사·잡담·기술 문제("들리세요?")만 생략한다.

## open-questions
`<li>` 마다 `<b>확인할 것</b>` + 왜 미결인지 + `<span class="time">[HH:MM]</span>`. 답이 나오지 않은 질문, "다음에 확인하자"고 한 항목. 확인할 조직·역할이 언급되었으면 표기(사람 이름은 쓰지 않는다). 바깥 `<ul class="list open">` 는 템플릿에 있다.

## references (코드가 채움)
meeting-research 단계의 `research.html` 을 그대로 붙인다.

## insights (코드가 채움)
meeting-insights 단계의 `insights.html` 을 그대로 붙인다.

## glossary (코드가 채움)
`analysis.json` 의 `terms` 를 언급 횟수 순으로 "용어 | 이 미팅에서의 뜻 | 녹취 표기" 3열로 넣는다.

## footer (코드가 채움)
템플릿 그대로.

## 금지
- `<script>`, `on*=` 속성, `javascript:` 링크, 외부 `src`/`@import`/`url(http...)`
- 외부 이미지. 모든 시각 요소는 인라인 SVG(`<title>` 필수)
- 허용 목록 밖의 태그. 쓸 수 있는 태그: html head body meta title style main header footer section article nav aside h1–h6 p div span ul ol li dl dt dd table thead tbody tfoot tr th td caption blockquote a b strong i em code pre br hr small sup sub details summary figure figcaption time abbr mark, SVG는 svg g path rect circle ellipse line polyline polygon text tspan title desc defs marker linearGradient stop clipPath (img·iframe·form·link·base·use·image·animate 등은 거절)
- 링크는 `https:`·`mailto:`·`#id`만. `http:`·`data:` 링크, `<meta http-equiv>`, SVG 안의 `<style>`, 인라인 `style="...url(...)"`, CSS 백슬래시 이스케이프, `url(#id)`가 아닌 `url()`, `<`·`>`가 들어간 주석
- 템플릿의 `{{...}}` 자리표시자를 남기는 것 (`{{DIAGRAM:...}}` 는 코드가 바꾼다)
- 화자 라벨(`<span class="spk">`), 번역 병기(`<span class="orig">`)
