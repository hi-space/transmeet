# 미팅 오디오 보관 + 종료 시 메일 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 녹음 오디오를 S3 에 보관해 언제든 MP3 로 내려받고, "미팅 종료" 시 본인 메일로 요약 + 다운로드 링크를 보낸다.

**Architecture:** 백엔드(ECS)가 WS 로 오는 PCM 을 연결별 버퍼에 모아 약 1분/정지/종료/연결 끊김/SIGTERM 마다 S3 part 로 올린다. 다운로드·종료 때 parts 를 시각순으로 이어 ffmpeg 로 MP3 를 만든다. 종료는 WS `endMeeting` 이 오디오 → 요약 → SES 발송을 끝까지 수행한다. 수신 주소 인증은 새 REST Lambda(`/me/email`)가 SES 개별 identity 로 처리한다.

**Tech Stack:** FastAPI + aiobotocore (S3, SESv2), ffmpeg, AWS CDK (aws-ses), Next.js 14.

**Spec:** `docs/superpowers/specs/2026-10-28-meeting-audio-and-email-design.md`

## Global Constraints

- 오디오 버킷 `transmeet-meeting-audio-{account}`, BLOCK_ALL, SSE-S3. part 키 `meetings/{meetingId}/parts/{ISO시각}-{connId[:8]}.pcm`, 결과 `meetings/{meetingId}/audio.mp3`.
- PCM: 16 kHz mono s16le. MP3 mono 64 kbps.
- 주기 flush 1,920,000 바이트(약 60초).
- 다운로드 서명 URL 300초, 메일 서명 URL 21600초(6시간) — 메일 문구에 "6시간 동안 유효".
- 발신 `TransMeet <no-reply@hi-yoo.com>`, 미팅 링크 `https://transmeet.hi-yoo.com/?meeting={meetingId}`.
- 수신자는 ID 토큰 `email` 클레임만. 클라이언트가 보낸 주소는 쓰지 않는다.
- 중복 메일: `emailSentAt` 조건부 쓰기. SES 실패 시 `emailSentAt` 제거.
- 요약 실패해도 메일은 보낸다(요약 자리에 "요약을 만들지 못했습니다. 미팅 화면에서 다시 시도해 주세요.").
- 소유하지 않은 미팅은 저장·다운로드·종료 모두 거절 (`_claim_meeting`).
- 녹음 경로를 막지 않는다: part 업로드는 `create_task`.

## Review Focus

- 녹음 중 다운로드: 현재 연결 버퍼를 먼저 flush 해 그 순간까지 포함 → `test_prepare_flushes_live_buffer`.
- part 가 하나도 없는 미팅 다운로드·종료 → "저장된 녹음이 없습니다" / 메일에 다운로드 버튼 없음 → `test_merge_no_parts_returns_none`, `test_email_without_audio`.
- 종료 연타·두 연결 동시 종료 → 메일 1통 → `test_end_meeting_sends_once`.
- SES 미인증 사용자 → 발송 안 하고 `phase:"email", status:"unverified"` → `test_end_meeting_unverified`.
- 미팅 제목·요약에 HTML 특수문자 → 메일 HTML 에서 이스케이프 → `test_email_escapes_html`.

---

### Task 1: `backend/app/audio_store.py` — part 저장·합치기·서명

**Files:** Create `backend/app/audio_store.py`, `backend/tests/test_audio_store.py`; Modify `backend/app/aws_clients.py` (add `s3_client`, `sesv2_client`), `backend/app/config.py` (`AUDIO_BUCKET=""`, `MAIL_FROM="TransMeet <no-reply@hi-yoo.com>"`, `APP_BASE_URL="https://transmeet.hi-yoo.com"`), `backend/Dockerfile` (apt ffmpeg).

**Interfaces (Produces):**
- `part_key(meeting_id: str, conn_id: str, now: datetime) -> str`
- `async upload_part(meeting_id: str, conn_id: str, pcm: bytes) -> Optional[str]` — 빈 바이트·버킷 미설정이면 None
- `async ensure_merged(meeting_id: str) -> Optional[str]` — mp3 키 또는 parts 없으면 None. mp3 의 LastModified 가 최신 part 이상이면 재생성 안 함. 미팅별 `asyncio.Lock`.
- `async presign(key: str, expires: int, filename: str) -> str` — `ResponseContentDisposition=attachment; filename*=UTF-8''...`
- `async delete_meeting_audio(meeting_id)` 는 Lambda 쪽에서 하므로 만들지 않는다.

- [ ] Tests: `test_part_key_sorts_by_time`, `test_upload_part_skips_empty`, `test_merge_concatenates_in_key_order` (fake S3 + 실제 ffmpeg, 결과 bytes 가 `ID3`/`\xff\xfb` 로 시작), `test_merge_reuses_fresh_mp3`, `test_merge_no_parts_returns_none` → FAIL
- [ ] Implement; ffmpeg 는 `asyncio.create_subprocess_exec("ffmpeg","-f","s16le","-ar","16000","-ac","1","-i","pipe:0","-b:a","64k","-f","mp3","pipe:1")`
- [ ] `pytest backend/tests/test_audio_store.py` PASS, commit

### Task 2: `backend/app/mailer.py` — 메일 구성·발송

**Interfaces (Produces):**
- `build_meeting_email(title: str, started_at: str, summary: Optional[str], audio_url: Optional[str], meeting_url: str) -> dict` → `{"subject","html","text"}`
- `async email_verified(email: str) -> bool` — SESv2 `GetEmailIdentity`, NotFound → False, `VerifiedForSendingStatus`
- `async send_meeting_email(to: str, mail: dict) -> None`
- 요약 마크다운 → HTML: 의존성 추가 없이 간단 변환(제목 `#`, 불릿 `-`/`*`, `**굵게**`, 나머지 문단). 먼저 `html.escape`.

- [ ] Tests: `test_email_includes_links`, `test_email_without_audio`, `test_email_summary_failed_text`, `test_email_escapes_html` → FAIL → implement → PASS, commit

### Task 3: ws_handler 통합 (버퍼·flush·prepare_audio·endMeeting·SIGTERM)

**Files:** Modify `backend/app/auth.py` (`verify_id_token_claims -> dict`; 기존 `verify_id_token` 은 sub 반환 유지), `backend/app/ws_handler.py`, `backend/app/main.py` (lifespan shutdown → `flush_all_audio()`); Test `backend/tests/test_meeting_end.py`.

**Interfaces:**
- `ConnectionState.user_email: str`, `audio_pcm: bytearray`
- `_buffer_audio(state, conn_id, pcm)` — 1,920,000 넘으면 `_flush_audio(state, conn_id)` 태스크
- `async _flush_audio(state, conn_id) -> None` — 버퍼 비우고 `upload_part`
- `async flush_all_audio() -> None`
- `async _end_meeting(send, state, conn_id, meeting_id, email_on_end: bool) -> None` — 이벤트 `{"type":"meeting_end","meetingId",phase:"audio"|"summary"|"email"|"done"|"error", status?}`; email status: `"sent"|"skipped"|"unverified"|"already_sent"|"failed"|"no_email"`
- WS actions: `prepare_audio` → `{"type":"audio_ready","meetingId","url"}` 또는 `{"type":"audio_ready","meetingId","error"}`; `endMeeting {meetingId, emailOnEnd}`
- `_stream_summary` 가 요약 문자열(또는 실패 시 None)을 반환하도록 변경.

- [ ] Tests: `test_prepare_flushes_live_buffer`, `test_end_meeting_sends_once`, `test_end_meeting_unverified`, `test_end_meeting_summary_failure_still_emails`, `test_end_meeting_email_off` → FAIL → implement → 전체 `pytest` PASS, commit

### Task 4: 인프라

**Files:** Modify `infra/lib/transmeet-stack.ts`, `infra/lambda/meetings/index.ts` (DELETE 시 `meetings/{id}/` 접두 객체 전부 삭제); Create `infra/lambda/email/index.ts`.

- 오디오 버킷 + 수명주기 없음. `wsTaskRole` Put/Get/List/Delete; lambdaRole List/Delete; meetingsFn env `AUDIO_BUCKET`.
- `ses.EmailIdentity('MailDomain', { identity: ses.Identity.publicHostedZone(hostedZone), mailFromDomain: 'mail.hi-yoo.com' })` + DMARC TXT `_dmarc` `v=DMARC1; p=none;` (이미 있으면 생략 — 배포 전 확인).
- EmailFn: `GET /me/email` → `{email,status}`; `POST /me/email/verify` → CreateEmailIdentity(존재하면 pending 일 때 삭제 후 재생성으로 재발송). 정책 `ses:GetEmailIdentity`, `ses:CreateEmailIdentity`, `ses:DeleteEmailIdentity`.
- wsTaskRole `ses:SendEmail`, `ses:GetEmailIdentity`; 컨테이너 env `AUDIO_BUCKET`, `MAIL_FROM`, `APP_BASE_URL`.
- [ ] `npx cdk synth` 성공, `npx tsc --noEmit` (infra) 성공, commit

### Task 5: 프런트

**Files:** Modify `src/hooks/useSettings.ts` (`emailOnEnd: boolean`, 기본 false), `src/hooks/useWebSocket.ts` (`endMeeting(meetingId, emailOnEnd)`, `prepareAudio(meetingId)`), `src/lib/api.ts` (`getEmailStatus()`, `requestEmailVerification()`), `src/components/SettingsPanel.tsx` (토글 한 줄 + `<EmailNotifySetting>`), `src/components/ControlPanel.tsx` (종료 버튼 슬롯), `src/app/page.tsx` (이벤트 처리·딥링크·다운로드 버튼); Create `src/components/EmailNotifySetting.tsx`, `src/components/MeetingAudioActions.tsx`.

- 딥링크: 첫 미팅 목록 로드 후 `?meeting=` 이 목록에 있으면 선택하고 `history.replaceState` 로 쿼리 제거.
- 종료 버튼: 녹음 중이면 `confirm`. 진행 표시 → 완료 토스트 문구: sent "메일을 보냈습니다", unverified "메일 인증이 필요합니다 — 설정에서 확인하세요", skipped 없음.
- 다운로드: `prepareAudio` → `audio_ready.url` 을 `window.location.href` 로.
- [ ] `npx tsc --noEmit`, `npm run build` 성공, 브라우저로 설정 토글/버튼 노출 확인, commit

### Task 6: 배포·검증

- [ ] 백엔드 이미지 빌드·ECR 푸시 → `cdk deploy` → force-new-deployment (메모리: 백엔드 이미지 푸시 → cdk deploy 순서)
- [ ] SES 도메인 identity Verified 확인, 본인 주소 인증 요청 → 실제 미팅 종료 메일 수신 확인
- [ ] 프런트 배포
