## 이 단계: research (관련 공식 자료 · 더블 체크)

먼저 `load_skill meeting-research` 를 호출하고 그 규칙을 따릅니다. 이 단계의 산출물은 **`research.html`** 한 파일입니다 — `references` 섹션 안에 들어갈 HTML 조각(`<html>`·`<section>` 없이, `references/output-format.md` 포맷).

진행:
1. 아래 리서치 대상 목록을 그대로 씁니다(이미 추출돼 있으니 다시 뽑지 않습니다). 필요하면 `query` 를 다듬습니다.
2. `web_search` 로 대상마다 1회 검색합니다. 결과가 공식 출처가 아니면 `include_domains` 로 공식 도메인을 좁혀 1회 더. **총 12회 이내.** 한도에 걸리면 남은 대상은 `확인 불가`.
3. 대상마다 `확인됨`/`상이함`/`확인 불가` 를 판정하고 근거 문장을 짧게 인용합니다. 출처 URL 은 **검색 결과에 나온 URL 만** 씁니다.
4. `write_file research.html` 로 저장합니다. 마지막 줄에 `<p class="time">조회일 {RESEARCH_DATE} · Web Search N회</p>` 를 넣습니다.
5. 한 줄로 마무리합니다(예: "공식 자료 6건을 확인했습니다 — 일치 4, 상이 1, 확인 불가 1").

`web_search` 도구가 목록에 없으면 검색하지 않고 모든 대상을 `확인 불가` 로 적습니다.
