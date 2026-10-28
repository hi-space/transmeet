# 시각 컴포넌트 — 모델이 HTML/SVG 로 직접 그리는 그림

핵심 요약(brief)의 `{{VISUAL}}`, 타임라인(timeline)의 역 안에서 쓴다. 템플릿 CSS(`page.css`)가 모양을 책임지므로 **클래스만 맞게 쓰면** 데스크톱·모바일·다크 모드가 모두 맞는다. 새 색상 코드·`style="..."` 속성은 쓰지 않는다.

## 무엇을 그릴지 고르기

회의에서 **글로 세 문장 넘게 설명해야 했던 구조**를 하나 고른다. 고르는 순서:

| 회의 내용 | 컴포넌트 |
|---|---|
| 시스템·조직이 층으로 나뉘거나 구성이 바뀜 (A 가 위, B 가 아래, 무엇을 무엇에 연결) | `.arch` 계층 구조 |
| 2~4개 대안을 놓고 고름 (채택안이 있으면 표시) | `.compare` 선택지 비교 |
| 지금 방식 → 바꿀 방식 | `.shift` 변경 전·후 |
| 순서가 있는 절차·로드맵·파이프라인 (3~6단계) | `.flow` 단계 흐름 |
| 위 넷으로 안 되는 관계(순환, 허브-스포크, 2×2 사분면) | 직접 그린 `<svg>` |

노드·라벨은 `analysis.json` 의 `terms` 정식 표기만 쓴다(녹취 표기·한글 음차 금지). 그림은 1~2개. 그릴 구조가 없으면(잡담 위주, 항목 2개 이하) 그리지 않고 `{{VISUAL}}` 을 빈 문자열로 바꾼다. 그림에 넣은 사실은 회의에서 실제로 나온 것만.

각 그림은 `<h2>` 제목(무엇을 보여주는지, 명사형 또는 질문형 12~20자) + `<div class="visual">` + 그 안 첫 줄 `<p class="why">` 한 문장(그림을 어떻게 읽는지).

## `.arch` 계층 구조

```html
<h2>바뀌는 구조</h2>
<div class="visual">
  <p class="why">로봇 안에서 연산을 두 층으로 나누고, 클라우드는 세 서비스로만 잇습니다.</p>
  <div class="arch">
    <div class="layer"><h5>클라우드</h5><div class="nodes">
      <span class="node">AWS IoT Core<small>원격 제어·텔레메트리</small></span>
      <span class="node">Amazon Kinesis Video Streams<small>영상 재생</small></span>
    </div></div>
    <p class="link">MQTT 원격 제어, 영상 스트림</p>
    <div class="layer"><h5>상위 연산</h5><div class="nodes">
      <span class="node key">Jetson Thor<small>YOLO·플래닝·SLAM</small></span>
      <span class="node new">상부 카메라<small>새로 장착</small></span>
    </div></div>
  </div>
</div>
```
- 층은 위에서 아래로 2~4개. 층 사이 `<p class="link">` 에 연결 방식·주고받는 것.
- `.node.key` = 이 회의의 중심 구성요소(1~2개), `.node.new` = 이번에 새로 들어오거나 바뀌는 것. `<small>` 은 역할 한 구절.

## `.compare` 선택지 비교

```html
<div class="compare">
  <div class="opt pick"><h5>SSD 직접 교체<span class="badge done">채택</span></h5>
    <ul class="pc"><li class="pro">1시간 주행분 영상도 바로 옮김</li><li class="con">사람이 매번 교체</li></ul></div>
  <div class="opt"><h5>Wi-Fi 자동 업로드</h5>
    <ul class="pc"><li class="pro">손이 안 감</li><li class="con">밤새 걸림</li></ul></div>
</div>
```
- `.opt.pick` 은 회의에서 고른 안(없으면 아무 것도 `pick` 하지 않는다). 배지 문구는 회의 결론대로(`채택`, `가장 빠름`, `보류`).
- 장단점은 회의에서 나온 것만, 각 1~3줄.

## `.shift` 변경 전·후

```html
<div class="shift">
  <div><h5>지금</h5><p>모델 학습을 로컬 PC 에서 반복</p></div>
  <div class="arrow" aria-hidden="true">→</div>
  <div><h5>바꾸면</h5><p>환경을 로컬에서 정의한 뒤 <mark>대규모 확장 테스트만 클라우드로</mark></p></div>
</div>
```

## `.flow` 단계 흐름

```html
<ol class="flow">
  <li><b>엣지 1차 필터</b><span>YOLO·decision tree</span></li>
  <li><b>신뢰도 확인</b><span>낮을 때만 다음 단계</span></li>
  <li class="pick"><b>Amazon Bedrock VLM</b><span>정밀 판정·보고서</span></li>
</ol>
```
- 3~6단계, `<b>` 는 10자 안팎, `<span>` 은 한 구절. 번호는 CSS 가 붙인다. 회의가 고른 단계는 `.pick`, 위험한 단계는 `.risk`.

## 직접 그린 `<svg>`

위 컴포넌트로 안 될 때만. 규칙(검증기가 강제):
- `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 H" role="img">` 바로 안에 `<title>` 필수, `<desc>` 권장. 너비 720 고정, 높이는 내용대로(≤ 480).
- 쓸 수 있는 요소: `g path rect circle ellipse line polyline polygon text tspan title desc defs marker linearGradient stop clipPath`. `<style>`·`<use>`·`<image>`·`<foreignObject>`·애니메이션 금지.
- 색은 `fill="var(--tm-accent)"` 처럼 `--tm-*` 변수만: `--tm-fg --tm-muted --tm-line --tm-card --tm-accent --tm-accent-soft --tm-ok --tm-ok-soft --tm-bad --tm-bad-soft --tm-warn --tm-warn-soft`. 글자는 `fill="currentColor"`.
- 글자 크기 15 이상(모바일에서 절반으로 줄어든다), `font-family="var(--tm-font, sans-serif)"`. 라벨이 상자를 넘지 않게 상자 폭 ≥ 글자 수 × 16.
- 화살표는 `<defs><marker id="...">` + `marker-end="url(#...)"`. 다른 `url()` 은 금지.
- 다 그렸으면 `bash python3 skills/meeting-minutes-page/scripts/validate_page.py page.html` 로 확인한다.

## 하지 말 것
- 숫자 KPI 타일, 장식용 아이콘·이모지, 회의에 없던 노드
- 한 그림에 노드 12개 초과
- 그림만 있고 같은 사실이 본문(핵심·결정·역)에 없는 것
