# references 섹션 출력 포맷

항목마다 아래 블록. 배지 클래스는 `ok` / `diff` / `na`.

```html
<div class="ref card">
  <div><a href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html">AgentCore Harness 개발자 가이드</a>
    <span class="badge ok">확인됨</span></div>
  <div class="why">미팅 [10:14] Speaker 1: "하네스는 모델·도구·스킬을 선언형으로 묶는다" — 문서의 개요 절과 일치.</div>
  <div class="why">공식 문서 · docs.aws.amazon.com · 2026-09 갱신</div>
</div>
```

`diff`일 때 `why`에 양쪽을 모두 적는다:

```html
<div class="why">미팅 [10:31]: "GA는 3월" / 출처: "Generally available since May 2026" → <span class="badge diff">상이함</span></div>
```

항목이 0개:

```html
<p class="time">리서치 대상이 되는 외부 사실이 없었습니다.</p>
```

마지막에 조회일 한 줄: `<p class="time">조회일 2026-10-08 · Web Search 7회</p>`
