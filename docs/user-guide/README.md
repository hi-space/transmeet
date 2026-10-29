# TransMeet HTML 사용 가이드

배포·전달용 파일은 [`public/guide/index.html`](../../public/guide/index.html)입니다.
브라우저에서 파일을 직접 열어 사용할 수 있고, 정적 파일 배포 시
`/guide/index.html`에서 접근할 수 있습니다. CSS, JavaScript, 로고와 스크린샷을
파일 안에 포함하므로 이미지 폴더나 인터넷 연결 없이 읽을 수 있습니다.

19개 장에서 처음 시작, 모든 주요 메뉴, 음성·번역·고급·테마·요약 양식·계정 설정,
이메일 인증, 모바일 사용법과 문제 해결을 다룹니다. 검색, 화면 번호 표시,
이미지 확대, 지시문 예시 복사, 인쇄/PDF 저장을 제공합니다.

## 화면의 출처

스크린샷 34장은 `src/app/page.tsx`와 실제 컴포넌트를 임시 프리뷰에 묶어
Playwright로 촬영했습니다. 실제 CSS·브랜드와 캐시된 폰트가 있으면 함께 사용합니다.
회의, 계정, 번역, 요약 및 회의 페이지는 설명용 예제입니다.
REST·WebSocket·인증 요청은 촬영 스크립트에서 가로채며, 실제 서비스 호출,
메일 발송, 페이지 공개 또는 계정 변경을 수행하지 않습니다.

스크린샷은 현재 UI의 증거이며 실제 음성 인식·AI 결과 품질·메일 전달의 검증은 아닙니다.
버튼 이름, 기본값과 표시 조건은 소스와 대조합니다. 앱이 변경되면 설명과 화면을 함께 갱신하세요.

## 수정과 재생성

본문은 `guide.template.html`, 스타일은 `guide.css`, 상호작용은 `guide.js`에서
수정합니다. 생성된 HTML을 직접 수정하면 다음 빌드 때 덮어씁니다.

```bash
# 스크린샷 없이 본문·스타일 변경만 반영
python3 docs/user-guide/build_guide.py

# UI가 바뀌었을 때 실제 화면 다시 촬영
python3 docs/user-guide/capture_screenshots.py

# 특정 화면만 갱신하고 나머지 캡처는 유지
python3 docs/user-guide/capture_screenshots.py --only account-settings,languages
python3 docs/user-guide/build_guide.py

# 오프라인 파일, 검색·확대, 모바일, 인쇄 확인
python3 docs/user-guide/verify_guide.py
```

촬영에는 프로젝트의 기존 Node 의존성, `infra/node_modules/esbuild`,
Python Playwright/Chromium 및 Pillow가 필요합니다. 가이드 빌드는 Python 표준 라이브러리만
사용합니다. 앱 소스·환경 설정·기존 빌드 산출물을 수정하지 않습니다.

`screenshots/manifest.json`에는 이미지 크기, 설명과 버튼 표시 위치를 기록합니다.
검증 결과, 데스크톱·모바일 미리보기, 인쇄 확인용 PDF는 `/tmp/transmeet-guide-qa`에 생성됩니다.

## 검토할 기준

- 로그인 화면과 `SettingsPanel.tsx`의 모든 현재 탭을 확인합니다.
- `useSettings.ts`, `theme.ts`, `summaryStyles.ts`에서 기본값과 제한을 확인합니다.
- `page.tsx`와 각 컴포넌트에서 버튼의 실제 행동을 확인합니다.
- 내 메모, 대화, 빠른 번역의 방향과 복사·재생 대상을 혼동하지 않습니다.
- 계정 이메일 인증과 종료 메일 수신 인증을 구분합니다.
- 회의 페이지의 공개 링크, 재생성과 공유 해제 동작을 명시합니다.
