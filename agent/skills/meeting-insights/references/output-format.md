# insights 섹션 출력 포맷

다섯 묶음을 `<h3>`로 나누고, 항목은 `.card`. 추론에는 `<span class="badge infer">추정</span>`. 근거는 `<span class="time">` 안에 시각과 앵커 링크.

```html
<h3>리스크</h3>
<div class="card">
  <p><b>GA 일정 전제가 공식 일정과 다르다.</b> 2분기 출시 계획이 "3월 GA"를 전제로 세워졌지만 공식 문서는 5월 GA로 안내한다. 계획을 그대로 두면 통합 테스트 기간이 사라진다.</p>
  <p class="time">근거: <a href="#topic-3">주제 3 [10:31]</a>, 관련 자료 #2 (상이함)</p>
</div>
<div class="card">
  <p><span class="badge infer">추정</span> 보안 리뷰 담당이 비어 있어 출시 2주 전 병목이 될 가능성이 높다.</p>
  <p class="time">근거: <a href="#topic-5">주제 5 [10:52]</a>, 미결 사항 1</p>
</div>
```

discussion 블록은 `id="topic-1"`부터 순서대로 부여되어 있어야 한다 (meeting-minutes-page가 `.topic`에 `id`를 넣는다).

묶음 순서: 시사점 → 리스크 → 기회 → 권장 후속 조치 → 다음 미팅 안건. 비어 있는 묶음은 `<h3>` 자체를 생략한다. 전체가 비면 `<p class="time">추가 인사이트 없음 — 결정과 액션 아이템이 논의를 충분히 반영합니다.</p>`.
