# 미팅 오디오 보관 + 종료 시 메일 전송 설계

2026-10-28

## 의도

지금은 녹음하면 발언 텍스트만 DynamoDB 에 남고 오디오는 Transcribe 로 흘려보낸 뒤 버린다.
사용자는 (1) 녹음 원본을 나중에 다시 받고 싶고, (2) 미팅이 끝나면 따로 앱을 열지 않아도 요약과
녹음 파일이 본인 메일함에 와 있기를 바란다.

## 목표

- 미팅 오디오를 S3 에 보관하고, 미팅 화면의 버튼으로 언제든 다운로드한다.
- "미팅 종료" 버튼을 새로 둔다. 누르면 녹음이 멈추고 그 미팅이 끝난 것으로 본다.
- 설정의 "종료 시 메일로 받기" 토글이 켜져 있으면, 종료 시 **로그인한 본인 Cognito 이메일**로
  요약 + S3 서명 URL(바로 다운로드) + 미팅 화면 링크를 보낸다.
- 토글을 켤 때 SES 개별 주소 인증을 시작하고, 인증 상태를 토글 옆에 보여준다.

## 비목표

- 다른 참석자에게 보내기, 수신자 지정. 받는 사람은 본인 한 명뿐이다.
- SES 프로덕션 액세스 신청 (샌드박스 + 개별 주소 인증으로 운영).
- 오디오 편집·구간 재생·발언과 오디오 시각 동기화.
- Whisper 경로의 오디오 보관은 같은 방식으로 되면 하되 필수 아님 (기본 STT 는 Transcribe).

## 반목표 (돌아가도 실패인 것)

- 녹음 도중 WS 가 끊기거나 ECS 태스크가 바뀌었다고 그때까지의 오디오가 통째로 사라지는 것.
- 메일이 같은 미팅에 여러 번 가는 것 (종료 버튼 연타, 재시도).
- 남의 미팅 오디오를 받을 수 있는 것 — 다운로드는 사용자별 미팅 격리(`owns_meeting`)를 그대로 따른다.
- 녹음·번역 지연이 늘어나는 것. 오디오 저장은 Transcribe 경로를 막지 않아야 한다.

## 제약

- SES(us-east-1)는 샌드박스. 발신·수신 모두 인증된 identity 여야 한다.
  - 발신: `hi-yoo.com` 도메인 identity. Route53 호스티드 존이 있으므로 CDK 로 Easy DKIM 레코드까지 생성.
    발신 주소는 `no-reply@hi-yoo.com` (빌더 판단으로 변경 가능). amazon.com 수신을 위해 SPF(MAIL FROM)·DMARC 도 둔다.
  - 수신: 사용자 이메일을 개별 identity 로 등록 → AWS 가 확인 메일 → 사용자가 클릭.
- 서명 URL 은 ECS 태스크 역할(임시 자격 증명)로 서명하므로 길어야 몇 시간 유효하다. 메일에
  "N시간 유효, 이후엔 미팅 화면에서 받기"라고 명시한다. 며칠짜리 링크는 범위 밖.
- 오디오는 브라우저가 700ms 단위 16kHz mono 16-bit WAV 로 WS 에 보낸다 (`useAudioCapture.ts`).
  Transcribe 경로는 `ws_handler.py` 의 `sendAudio` 에서 44바이트 헤더를 떼고 PCM 을 큐에 넣는다.
- 백엔드 배포는 deploy.sh 에 없다 — ECR 수동 빌드/푸시 + force-new-deployment.

## 접근법

| | 방식 | 장점 | 단점 |
|---|---|---|---|
| **A (선택)** | 백엔드가 WS 로 들어오는 PCM 을 모아 S3 에 구간 파일로 저장, 종료 시 합쳐 MP3 로 변환 | 이미 서버로 오는 오디오 재사용, 프런트 변경 최소, Transcribe 가 들은 소리와 동일, 탭을 닫아도 남음 | Docker 에 ffmpeg 추가, 마지막 미저장 구간은 끊기면 손실(주기 저장으로 완화) |
| B | 브라우저 `MediaRecorder` 로 별도 녹음 후 종료 시 업로드 | 파일 작음, 서버 부담 없음 | 업로드 전 탭 닫으면 전부 손실, 마이크+시스템 오디오 믹싱 경로 신설, 브라우저별 포맷 차이 |

사용자가 A 를 골랐다. 이유: 오디오가 이미 서버로 오고 있고, 탭을 닫아도 그때까지 녹음이 남는다.

## 사용자가 정한 것

- 메일 수신자: 로그인한 본인 이메일.
- 메일 내용: 요약 + S3 서명 URL + 미팅 화면 링크.
- 종료 시점: 명시적인 "미팅 종료" 버튼. 녹음 정지가 아니다. 메일은 종료 때 한 번.
- 수신 주소 인증: SES 개별 주소 인증 (amazon.com 등 도메인 인증이 불가능한 주소도 이 방식).
- 오디오 수집: 접근법 A.
- 프런트에 녹음 파일 다운로드 버튼이 있다. 미팅 종료 전에도 그때까지의 녹음을 받을 수 있다.

## 동작 설계

### 오디오 저장 (백엔드)

- 버킷: 새 `transmeet-meeting-audio-{account}` (BLOCK_ALL, SSE-S3). 페이지 버킷과 분리 — 페이지 버킷은 CloudFront 로 공개된다.
- `ConnectionState` 에 PCM 바이트 버퍼를 둔다. `sendAudio` 에서 Transcribe 큐에 넣는 것과 별개로 버퍼에 append.
- 버퍼는 다음 때 S3 에 구간 객체로 올린다: `stopRecording`, 일정 크기/시간 초과,
  WS 연결 종료(finally), `endMeeting`, 그리고 프로세스 종료(SIGTERM → FastAPI lifespan shutdown 에서
  활성 연결 버퍼를 전부 flush; ECS 기본 stopTimeout 30초 안에 끝난다). 주기 flush 는 약 1분(≈1.9MB)으로 해서
  예기치 않은 종료 때 손실을 1분 이내로 둔다. *(내 판단)* 재접속 후 녹음 자동 재개는 지금 동작 그대로이며 범위 밖. 업로드는 `create_task` 로 비동기 — 녹음 경로를 막지 않는다.
- 키: `meetings/{meetingId}/parts/{ISO시각}-{connId 앞 8자}.pcm`. 시각순 정렬로 이어 붙일 수 있게.
  여러 연결·여러 태스크에서 와도 키가 겹치지 않는다.
- `meeting_id` 가 없거나 소유 확인이 안 된 연결은 저장하지 않는다 (발언 저장과 같은 규칙).

### 미팅 종료 (`endMeeting` WS 액션)

1. 녹음 중이면 `stopRecording` 과 같은 마무리 (Transcribe 종료, 세그먼트 flush, 오디오 버퍼 flush).
2. `endedAt` 을 기록한다 (덮어써도 됨 — 종료 시각 표시용). 중복 메일 방지는 별도 `emailSentAt` 으로 한다:
   발송 직전 `attribute_not_exists(emailSentAt)` 조건부 쓰기로 "발송권"을 얻은 쪽만 보낸다.
   SES 가 실패하면 `emailSentAt` 을 지워 다음 종료 시도에서 다시 보낼 수 있게 한다. *(내 판단)*
   종료 후 다시 녹음하고 다시 종료하면 오디오는 새로 합치지만 메일은 이미 갔으므로 다시 보내지 않는다. *(내 판단)*
3. 오디오 합치기: parts 를 시각순으로 내려받아 이어 붙이고 ffmpeg 로 MP3(mono, 64kbps 제안) 변환 →
   `meetings/{meetingId}/audio.mp3`. parts 는 남겨도 되고 지워도 된다 (빌더 판단; 남기면 재생성 가능).
   parts 가 없으면 오디오 없이 진행.
4. 요약: 기존 `_stream_summary` 경로를 재사용 (결과를 미팅 연결 전부에 브로드캐스트하고 DynamoDB 저장).
   요약이 두 모델 모두 실패해도 메일은 보낸다 — 본문에 "요약을 만들지 못했습니다. 미팅 화면에서 다시 시도해 주세요"
   와 오디오·미팅 링크를 넣는다. 오디오 링크가 메일의 핵심 가치라 요약 실패로 막지 않는다. *(내 판단)*
5. 메일 토글이 켜져 있고(`emailOnEnd: true` 를 payload 로 받음) 사용자 이메일이 SES 에서 인증됨이면 발송.
   인증 안 됐으면 보내지 않고 클라이언트에 이유를 알린다.
6. 진행 상황을 `{ type: "meeting_end", phase: "audio"|"summary"|"email"|"done"|"error", ... }` 로 알린다.
   소켓이 바뀌어도 끝까지 수행 (`_stream_summary` 가 브로드캐스터를 쓰는 것과 같은 이유).

### 메일

- 제목: `[TransMeet] {미팅 제목} 요약`.
- 본문(HTML + 텍스트 대체): 미팅 제목·일시, 요약(마크다운 → HTML), "녹음 파일 다운로드 (N시간 유효)" 버튼(서명 URL),
  "미팅 화면에서 보기" 링크. 오디오가 없으면 다운로드 버튼 생략.
- 미팅 화면 링크: `https://transmeet.hi-yoo.com/?meeting={meetingId}`. 프런트에 이 쿼리로 해당 미팅을
  여는 딥링크 처리를 새로 넣는다 (지금은 없다). 로그인 전이면 로그인 후 그 미팅으로.
- 발송: SES v2 `SendEmail`, 발신 `no-reply@hi-yoo.com`.

### 이메일 인증 (설정 토글)

- 사용자 이메일은 ID 토큰의 `email` 클레임에서 얻는다 (서버가 토큰에서 읽는다 — 클라이언트가 주는 주소를 믿지 않는다).
- REST(meetings Lambda, Cognito authorizer 클레임 사용):
  - `GET /me/email` → `{ email, status: "verified" | "pending" | "none" }` (SES `GetEmailIdentity`)
  - `POST /me/email/verify` → SES `CreateEmailIdentity`(이미 있으면 상태만 반환). pending 이면 재발송.
- 토글을 켜면 POST → "인증 메일을 보냈습니다. 메일함에서 링크를 눌러 주세요" + 상태 표시. 설정을 열 때 GET 으로 갱신.
- 토글 값 자체는 기존처럼 localStorage 설정(`emailOnEnd`)에 둔다. 종료 시 `endMeeting` payload 로 전달.
- 백엔드 발송 시 인증 상태는 SES 에 다시 묻는다 (`GetEmailIdentity`) — 샌드박스에서 미인증이면 SES 가 거절한다.

### 프런트

- "미팅 종료" 버튼: 녹음 버튼 근처(ControlPanel). 확인 없이 바로 vs 한 번 확인은 빌더 판단(제안: 녹음 중이면 확인).
  종료 진행 상태를 표시하고, 끝나면 "메일을 보냈습니다" 또는 "메일 인증이 필요합니다".
- "녹음 파일 다운로드" 버튼: 미팅 화면(요약/페이지 패널 근처). **종료 전에도 받을 수 있다 (사용자 결정).**
  누르면 WS `prepare_audio` → 백엔드가 `audio.mp3` 가 최신인지 본다(가장 늦은 part 의 LastModified 보다 늦으면 최신).
  최신이 아니면 parts 를 즉석에서 합쳐 다시 만든다(1시간 미팅이면 수 초 — 버튼에 "준비 중" 표시).
  녹음 중이면 먼저 현재 버퍼를 flush 해 그 순간까지를 포함한다. 끝나면 `{type:"audio_ready", url}` (서명 URL 5분) → 다운로드.
  parts 가 하나도 없으면 "저장된 녹음이 없습니다". ffmpeg 가 ECS 에만 있으므로 REST(Lambda)가 아니라 WS 로 한다.
  합치기 로직은 `endMeeting` 3단계와 같은 함수를 쓰고, 같은 미팅의 합치기가 겹치지 않게 미팅별 asyncio.Lock 을 둔다.
- 설정 패널: "종료 시 메일로 받기" 토글 + 인증 상태 + 본인 이메일 표시.

### 인프라 (CDK)

- 오디오 버킷 + `wsTaskRole` 에 Put/Get/List/Delete, meetings Lambda 에 List/Delete(미팅 삭제 시 오디오도 삭제).
- SES: `EmailIdentity` (hi-yoo.com, Route53 으로 DKIM 자동), MAIL FROM 서브도메인, DMARC TXT.
- `wsTaskRole`: `ses:SendEmail`, `ses:GetEmailIdentity`. meetings Lambda: `ses:CreateEmailIdentity`, `ses:GetEmailIdentity`.
- API: `/me/email` GET, `/me/email/verify` POST.
- 백엔드 env: `AUDIO_BUCKET`, `MAIL_FROM`, `APP_BASE_URL`. Dockerfile 에 ffmpeg.

## 빌더에게 맡기는 것

- 구간 flush 주기·크기, MP3 비트레이트, parts 정리 여부.
- 메일 HTML 디자인 세부.
- 종료 후 다시 녹음했을 때의 재종료 처리(`endedAt` 해제 여부).
- 미팅 삭제 시 오디오 삭제(해야 한다 — 방법은 빌더 판단).
