# Summary Styles Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Offer 간결하게, 자세하게, 할 일 중심, 공유·보고용 while keeping the current detailed summary and automatic triggers as the default.

**Architecture:** Keep a single server-side prompt manifest, consumed by Python WebSocket summaries and the TypeScript REST fallback. Persist each meeting's selected `summaryStyle` independently from the `summaryGeneratedStyle` of its existing summary. Pass the selection through manual, automatic, and meeting-end requests.

**Tech Stack:** Existing Next.js/React/TypeScript, Python/FastAPI, DynamoDB, AWS CDK; no new dependencies.

**Spec:** Approved in chat on 2026-10-28: four proposed presets, default to the current detailed behavior, remember choices per meeting.

## Global Constraints

- `detailed` is the fallback for missing or invalid stored values; its prompt and model settings stay unchanged.
- Presets are `concise`, `detailed`, `actions`, and `report`.
- Changing a selection saves it but does not immediately generate a summary.
- Every generation reads the original conversation.
- Existing one-line briefs, meeting pages, title generation, and unrelated working changes retain their behavior.
- Preserve source-only facts, unknown owners/deadlines, and the existing Korean noun-phrase style.
- Work from an isolated snapshot of the current workspace; apply only this feature's diff back to the shared workspace.

## Review Focus

- Old meetings without style metadata still show and generate detailed summaries.
- A failed preference save leaves the previous selection and summary intact.
- Selecting a new style does not relabel an existing summary.
- Manual, automatic, meeting-end, and REST requests carry the same selection.
- A late summary event cannot attach another meeting's content or generated style to the current meeting.

### Task 1: Shared prompt definitions

**Files:** `backend/app/summary_prompt.py`, new `backend/app/summary_styles.json`, new `src/lib/summaryStyles.ts`, new `infra/lambda/shared/summaryPrompt.ts`, `backend/tests/test_summary_prompt.py`.

**Interfaces:** `normalizeSummaryStyle(value: unknown): SummaryStyle`; Python `normalize_summary_style(value)` and `build_summary_prompt(title, transcript, style="detailed")`; TypeScript `buildSummaryPrompt(title, transcript, style)`.

- [x] Add failing tests for preset selection, defaults, original transcript inclusion, and malformed values.
- [x] Extract the detailed prompt unchanged; define distinct structures for the other presets.
- [x] Verify Python tests and identical Python/TypeScript prompt output.

### Task 2: Persistence and all server entry points

**Files:** `backend/app/ws_handler.py`, `infra/lambda/summarize/index.ts`, `infra/lambda/meetings/index.ts`, `infra/lib/transmeet-stack.ts`, associated Python tests and new `tests/summaryStyles.test.mjs`.

**Interfaces:** authenticated `PUT /meetings/{id}/summary-style` with `{summaryStyle}`; summary requests accept `summaryStyle`; results expose `summaryGeneratedStyle`; stream events expose `meetingId` and `summaryStyle`.

- [x] Add failing tests for saved/explicit/default styles and preference validation.
- [x] Wire WebSocket, REST, and meeting-end generation to the same preset.
- [x] Save the selected style separately from the generated style; retain ownership checks.
- [x] Verify endpoint behavior, meeting-end forwarding, and Lambda bundles.

### Task 3: Selection UI and request routing

**Files:** `src/components/SummaryPanel.tsx`, `src/app/page.tsx`, `src/types/meeting.ts`, `src/lib/api.ts`, `src/lib/websocket.ts`, `src/hooks/useWebSocket.ts`, new browser tests, `README.md`, `package.json`.

**Interfaces:** panel props for selection, generated style, saving/error state, and change callback; API and WebSocket helpers accept optional `SummaryStyle`.

- [x] Add failing behavior tests for round trips and outgoing request payloads.
- [x] Add an accessible selector using the existing input styling and show the actual generated style.
- [x] Save selections without regeneration; use them in all existing summary triggers.
- [x] Route summary events by meeting ID and prevent style changes during generation.
- [x] Verify desktop/mobile, reload, save failure, automatic and manual generation, and meeting end in a mocked browser.
- [x] Run existing frontend/backend suites, production build, and a fresh code review.
- [x] Apply only the reviewed feature diff back to the shared workspace.

## Verification record

- Legacy detailed user/system prompts compared byte-for-byte with the pre-change implementation.
- Node tests: 71 passed; backend tests: 208 passed (existing Pydantic deprecation warnings).
- Five browser scenarios passed against the production static export: selection/save failure/reopen, periodic summarization, meeting end, REST fallback, and delayed meeting loading.
- Next.js production export, frontend/infra type checks, and both Lambda bundles passed.
- Fresh code review found three edge cases; all were reproduced and fixed: lock the selector during meeting end, wait for meeting details before ending, and retain stop-triggered summaries during a preference save.
- Newer unrelated workspace edits were preserved through three-way merges before integration checks.

## Production deployment — 2026-10-28

- Deployed to `https://transmeet.hi-yoo.com`; CloudFormation update and CloudFront invalidation completed.
- WebSocket service: task definition revision 18, image tag `summary-styles-20261028-132310-v2`.
- Retained concurrent production updates for speech processing, email authentication refresh, and clipboard controls.
- Authenticated API checks passed for all four selections, preserved existing summaries, invalid selections (400), and unauthenticated requests (401).
- Live model checks passed: default REST detailed summary and WebSocket concise summary.
- Authenticated browser checks passed for the detailed default, four choices, saving, reopening, and mobile layout, with no runtime errors.
- Verified that the public HTML matches the release build and that previous static assets remain available.
- Removed the temporary verification account, its two meetings, and temporary authentication credentials.
- Deployment evidence: `/tmp/transmeet-summary-deploy-kf7vj0h3/release.json`.
