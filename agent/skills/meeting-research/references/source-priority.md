# 출처 우선순위

1. **벤더 공식 문서** — docs.aws.amazon.com, learn.microsoft.com, cloud.google.com/docs, developer.apple.com, 각 프로젝트의 공식 docs 사이트
2. **표준 기구** — rfc-editor.org, w3.org, iso.org, nist.gov, ietf.org
3. **벤더 공식 블로그·What's New·릴리스 노트** — aws.amazon.com/blogs, aws.amazon.com/about-aws/whats-new, github.com/<org>/<repo>/releases
4. **공식 보도자료·규제기관** — 회사 뉴스룸, 정부 기관 .gov/.go.kr
5. **그 외** — 기술 블로그, 뉴스, Stack Overflow. 1~4가 없을 때만, 그리고 배지는 `na`로 두고 "비공식 출처"라고 적는다.

같은 순위 안에서는 발행일이 최신인 것을 고른다. 결과에 `publishedDate`가 있으면 항목에 표기한다.

## 도메인 필터 예

AWS 관련 발언: `{"domainFilter": {"include": ["docs.aws.amazon.com", "aws.amazon.com"]}}`
Kubernetes: `{"domainFilter": {"include": ["kubernetes.io", "github.com"]}}`
