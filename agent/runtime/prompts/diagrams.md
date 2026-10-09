## 이 단계: diagrams (논의 다이어그램)

먼저 `load_skill meeting-diagrams` 를 호출합니다. 주제 타임라인(`diagrams/timeline.svg`)은 코드가 이미 만들었으니 다시 만들지 않습니다. 이 단계에서는 아래 **다이어그램 계획**에 있는 `flow`/`relations` 항목만 만듭니다.

진행(항목마다):
1. 스킬의 입력 JSON 형식으로 `write_file diagrams/<이름>.json` (이름은 영문 소문자·하이픈, 예: `decision-flow`). 노드 12개 이하, 라벨 14자 이내, `decision` 은 질문형, `outcome` 은 결과형.
2. `bash python3 skills/meeting-diagrams/scripts/flow_svg.py diagrams/<이름>.json > diagrams/<이름>.svg` (relations 도 `flow_svg.py` 로 그립니다 — 구성요소를 `step`, 관계를 엣지 라벨로).
3. 오류가 나면 JSON 을 고쳐 다시 실행합니다. 출력 SVG 를 열어 색상 코드를 바꾸지 않습니다.

마지막에 `write_file diagrams/manifest.json` 으로 아래 형식의 배열을 저장합니다(타임라인은 넣지 않습니다. 코드가 추가합니다):
```json
[{"file": "decision-flow.svg", "kind": "flow", "caption": "출시 결정 흐름 — 5월 GA 를 전제로 2분기 출시 [10:31]", "topic": 3}]
```
`topic` 은 이 다이어그램이 들어갈 주제 블록 번호(1부터). 한 줄로 마무리합니다.
