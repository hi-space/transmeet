## 이 단계: insights (인사이트 보고서)

먼저 `load_skill meeting-insights` 를 호출하고 그 작성 순서를 따릅니다(`references/insight-frames.md` 와 `references/output-format.md` 를 읽습니다). 산출물은 **`insights.html`** 한 파일 — `insights` 섹션 안에 들어갈 HTML 조각(`<section>` 없이).

입력:
- 트랜스크립트(아래), 주제 분할(`outline.json` 의 `topics` — discussion 블록은 이 순서로 `id="topic-1"`, `topic-2`… 가 됩니다. 근거 앵커는 이 번호를 씁니다).
- 리서치 결과(아래 `research.html` 내용). `상이함` 판정 항목은 반드시 리스크 또는 후속 조치로 다룹니다.

규칙: 사실과 추론을 구분하고 추론에는 `<span class="badge infer">추정</span>`. 각 항목에 근거(발언 시각 `[HH:MM]` + `<a href="#topic-N">`). 뻔한 일반론은 지웁니다. 남는 것이 적어도 억지로 채우지 않습니다. 전체가 비면 포맷 문서의 "추가 인사이트 없음" 문장을 씁니다.

`write_file insights.html` 로 저장하고 한 줄로 마무리합니다.
