---
name: meeting-diagrams
description: 미팅록 페이지에서 논의 내용을 그림으로 설명할 때 사용한다. 주제 타임라인(필수 1개)과 결정 흐름도·관계도(필요한 경우)를 번들 스크립트로 인라인 SVG로 만들고, 그릴지 말지 판단 기준과 삽입 형식을 정한다. "다이어그램", "타임라인", "흐름도", "그림으로 설명", "시각화" 요청이나 meeting-minutes-page 스킬의 5단계에서 로드한다.
---

# Meeting Diagrams

글로 세 문장 넘게 설명해야 하는 **순서·분기·관계**를 그림 하나로 줄인다. 그림이 글을 대신하지는 않는다. 그림 아래에는 항상 본문이 있다.

## 언제 그리나

`references/diagram-types.md`의 표를 따른다. 요약하면: 타임라인은 항상, 흐름도는 조건 분기가 있는 결정에, 관계도는 3개 이상 구성요소의 상호작용에. 항목 2개 이하·단순 나열은 그리지 않는다. 페이지당 최대 6개.

## 어떻게 그리나

손으로 SVG를 쓰지 않는다. 입력 JSON을 만들고 스크립트를 실행한다. 레이아웃·색·접근성 텍스트는 스크립트가 책임진다.

```bash
python3 scripts/timeline_svg.py timeline.json > timeline.svg
python3 scripts/flow_svg.py decision.json > decision.svg
```

스크립트는 `bash` 도구로 작업 디렉터리에서 실행한다 (`python3 skills/meeting-diagrams/scripts/flow_svg.py diagrams/decision.json > diagrams/decision.svg`). 캡션(한 문장 + 발언 시각)은 `diagrams/manifest.json`에 적는다. 페이지 조립 단계가 `{{DIAGRAM:decision.svg}}` 마커 자리에 `<figure>`와 캡션을 붙여 SVG를 인라인한다.

### 타임라인 입력
```json
{"start":"10:00","end":"11:00","topics":[{"label":"킥오프 & 목표","start":"10:00","end":"10:12"}]}
```
주제명은 discussion의 `<h3>`와 같은 문구. 12자 이내로 줄인다.

### 흐름도 입력
```json
{"title":"출시 결정 흐름","nodes":[{"id":"a","label":"GA 일정 확인","kind":"step"},{"id":"b","label":"5월 GA?","kind":"decision"},{"id":"c","label":"2분기 출시","kind":"outcome"}],"edges":[{"from":"a","to":"b"},{"from":"b","to":"c","label":"예"}]}
```
노드 12개 이하. 라벨 14자 이내. `decision`은 질문형, `outcome`은 결과형.

## 규칙

- 색은 스크립트가 넣는 CSS 변수(`--tm-*`)만. 출력 SVG를 열어 색상 코드를 바꾸지 않는다.
- 모든 SVG에 `<title>`이 있어야 한다. 스크립트 출력은 이미 포함한다. 검증기가 없는 것을 거절한다.
- 미팅에서 **실제로 논의된** 분기·관계만 그린다. 더 그럴듯하게 보이려고 노드를 추가하지 않는다.
- 다이어그램에 넣은 사실은 본문에도 있어야 한다.
