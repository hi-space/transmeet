# Email verification from notification settings

> **For agentic workers:** Implement inline with `superpowers:executing-plans`; use a fresh final review.

**Goal:** Turning on meeting emails starts any required verification inside Settings, and Settings reports when delivery is ready.

**Architecture:** Keep Cognito's verified-email requirement. Use the existing browser SDK to verify the account email, refresh its tokens, and then use the existing SES verification endpoint. Refresh the identity on an existing WebSocket without interrupting recording.

**Tech Stack:** React 18, Next.js, amazon-cognito-identity-js, Python/FastAPI, existing SES REST endpoint.

**Spec:** User request: “메일 인증은 어떻게해? 설정에서 옵션 켜면 인증하는것도 포함해야하지 않아?”

## Global constraints

- Preserve the existing notification opt-in and the current application styling.
- Never mark an email verified without completing the provider's verification.
- Do not send real emails during automated tests.
- A refreshed WebSocket token must belong to the same user as the connection.
- Existing unrelated working-tree changes stay intact; do not commit or deploy them.
- The notification preference can remain on while verification is pending, but the UI must clearly say delivery is not ready.

## Review focus

- Enabling before the initial status request finishes must still start verification exactly once.
- Disabling or unmounting during a status request must prevent subsequent email requests.
- Invalid/expired codes and failed email requests must leave a usable retry path.
- Existing pending SES verification must not be resent by polling, focus changes, or reopening Settings.
- Authentication refresh must preserve the current recording and reject another user's token.

## Task 1: Account email verification and live identity refresh

**Files:** `src/lib/cognito.ts`, `src/hooks/useWebSocket.ts`, `backend/app/ws_handler.py`, `tests/emailVerification.test.mjs`, `backend/tests/test_auth.py`.

**Interfaces:**
- `getAccountEmail(): Promise<{ email: string; verified: boolean }>`
- `sendAccountEmailVerificationCode(): Promise<void>`
- `verifyAccountEmailCode(code: string): Promise<void>`
- A browser session-refresh event carries the new ID token.
- WebSocket `refreshAuth` verifies the new token and updates only the existing user's verified email.

- [x] Add failing tests for verification, token refresh, missing sessions, and same-user WebSocket refresh.
- [x] Implement the SDK wrappers and the authenticated WebSocket update.
- [x] Run `node --test tests/emailVerification.test.mjs` and `python3 -m pytest tests/test_auth.py tests/test_mailer.py` from `backend`.

## Task 2: Settings verification flow

**Files:** `src/components/EmailNotifySetting.tsx`, a focused verification hook if needed, and browser tests under `tests`.

- [x] Add a browser test that fails when a newly enabled, unverified account cannot request and submit its code.
- [x] On enable, load current state and request the missing verification. Show an account-code form when necessary.
- [x] After account confirmation, start SES verification when needed. Explain the email-link step.
- [x] Check pending SES status on focus, periodically while visible, and through a manual check button.
- [x] Keep loading, failure, retry, cancellation, and already-verified states usable.
- [x] Verify the full flow, an initial-request race, cancellation, retries, pending verification, and an existing live socket using intercepted provider requests.

## Task 3: Verification and documentation

- [x] Document the user flow and the two provider checks in the README.
- [x] Run the relevant Node tests, backend suite, TypeScript checks, lint, and browser tests.
- [x] Review desktop/mobile rendering and the final scoped diff.
- [x] Obtain a fresh review, fix material findings, and report the local/deployed status accurately.

## Execution notes

- The current checkout contains active unrelated work. Apply only scoped edits in place, without a branch switch or commits.
- Repository knowledge-base access requires authentication; source code is the evidence for this change.
- Verification: 52 Node tests, 208 backend tests, and 11 browser scenarios passed; scoped and full-project TypeScript checks, scoped lint, formatting, and `git diff --check` passed.
- The full-project TypeScript check initially failed while an unrelated clipboard module was being written; it passed after that module was completed.
- Fresh review identified a stale connection when another tab had already refreshed shared tokens. Added failing unit/browser regressions, then published the current session after successful account checks; both regressions and the full suites passed.
- No deferred review findings. Automated email tests intercepted provider requests.
- Deployed on 2026-10-28: frontend cache invalidation completed; backend revision 17 preserves the concurrent summary-style release and includes email authentication refresh. Production login/WebSocket and published UI checks passed without test emails. The user confirmed successful operation.
