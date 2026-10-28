# Speech Language Controls Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this approved plan task by task. Preserve the existing uncommitted work.

**Goal:** Let users select an input language independently of the translation destination, and make all four STT integrations accurately reflect their language controls.

**Architecture:** Keep `sourceLang` for recognition and introduce `translationTarget` (`auto`, `ko`, `en`) for translation. Migrate legacy settings and accept legacy WebSocket messages without changing their meaning. Resolve the effective input language through the selected provider, and preserve detected language independently of the user's preference.

**Tech Stack:** Next.js 14 / React / TypeScript, FastAPI / Python, amazon-transcribe 0.6.4, SageMaker.

**Spec:** Approved conversation, 2026-10-28: separate input and translation controls; use Transcribe multilingual identification; pass the selected language to Whisper; retain Nemotron controls; show Qwen as automatic-only until its forcing contract is verified; preserve actual detected language; verify Korean, English, and alternating speech.

## Global Constraints

- Work from the current files, including unrelated in-progress UI and summary changes.
- Baseline snapshots and review diff: `/tmp/transmeet-language-settings-20261028-ujvpwqb0`.
- Baseline checks: 74 frontend tests; 253 backend tests; existing Pydantic deprecation warnings.
- Keep the default new-session behavior English input → Korean translation.
- Qwen's effective input is `auto`; retain the user's preferred input for other engines.
- Legacy AUTO settings/messages continue translating to the opposite language.
- Explicit translation destinations apply independently of recognition mode.
- If source and destination are the same, retain the original text instead of asking a model to rewrite it.
- Buffer draining, provider readiness, diarization, meeting ownership, and existing meeting workflows remain intact.
- No deployment or infrastructure changes are part of this implementation.

## Review Focus

- Saved legacy AUTO settings must not become fixed Korean translation.
- Selecting Qwen and returning to another provider must not erase the preferred input language.
- A selected input language must not overwrite a different language actually detected by STT.
- Changing the translation target during recording must drain buffered audio using the old settings.
- Mixed-language final/partial results and same-language destinations must produce consistent translations.

## Task 1: Language policy and migration

**Files:** Create `src/lib/speechLanguages.ts`, `backend/app/language_settings.py`, `tests/speechLanguages.test.mjs`, and `backend/tests/test_language_settings.py`.

**Interfaces:**
- `SourceLanguage`, `TranslationTarget`: `auto | ko | en`.
- `normalizeSpeechLanguages(saved)` returns `{ sourceLang, translationTarget }`, including legacy `targetLang` migration.
- `effectiveSourceLanguage(provider, preferred)` returns automatic for Qwen, otherwise the preference.
- `resolveDirection(sourceLang, translationTarget, detected)` returns concrete `{ source, target }`, preferring detected language.
- Python helpers normalize incoming settings and resolve explicit/automatic translation destinations; legacy callers remain supported.

- [x] Write table-driven behavioral tests for migration, effective provider input, and independent translation.
- [x] Observe failing tests, implement the helpers, and pass the targeted tests.

## Task 2: Provider and translation integration

**Files:** Modify `backend/app/stt.py`, `backend/app/ws_handler.py`, `backend/scripts/benchmark_stt.py`, `backend/tests/test_stt.py`, `backend/tests/test_transcribe_diarization.py`, `backend/tests/test_stt_benchmark.py`; add integrated language tests.

- [x] Test Whisper fixed-language JSON requests and automatic binary compatibility.
- [x] Test Transcribe multilingual request parameters and alternating Korean/English results with speaker labels.
- [x] Test detected-language preservation, explicit targets, legacy AUTO behavior, same-language output, and draining old settings.
- [x] Change Transcribe and its benchmark to multilingual identification.
- [x] Pass Whisper's `language` / `task=transcribe` through the existing endpoint's JSON contract.
- [x] Apply provider-effective source language and independent translation target at session start.
- [x] Use actual detected language for translation and emit it consistently; reuse original text for same-language targets.
- [x] Run the backend suite.

## Task 3: UI, settings, and recording wiring

**Files:** Modify `src/hooks/useSettings.ts`, `src/hooks/useWebSocket.ts`, `src/app/page.tsx`, `src/components/Header.tsx`, `src/components/SettingsPanel.tsx`, `package.json`; add a speech-control component and browser checks as needed.

- [x] Migrate saved speech settings without affecting unrelated preferences.
- [x] Replace the coupled direction selector with independently labelled input and destination selectors on desktop and mobile.
- [x] Display Qwen's effective automatic input with an explanation, and display the actual detected language.
- [x] Send `translationTarget` and effective `sourceLang`; restart/drain recording when input, target, or provider changes.
- [x] Use the same direction policy for partial and manual translations.
- [x] Verify persistence, provider switching, mixed-language direction, no horizontal overflow, and recording transitions in a real browser with controlled services.

## Task 4: Documentation, verification, and review

**Files:** Modify `docs/stt-models.md` and this plan.

- [x] Document independent controls, legacy migration, supported language forcing, and mixed-language limitations.
- [x] Run frontend tests, backend tests, TypeScript validation, production build, and targeted browser checks.
- [x] Perform a fresh code review of this change against the saved baseline and resolve material findings.
- [x] Report verified behavior and distinguish controlled tests from live inference.

## Execution Ledger

- Approved scope is the prior proposal; implementation proceeds inline without another approval round.
- Work in the shared checkout: moving only committed files to a new worktree would omit the current UI and backend behavior this change must preserve.
- Use the saved baseline for review and leave existing changes uncommitted.
- Only provider and language settings are in scope; no new language support beyond Korean and English is implied.
- Ruling: persist `targetLanguage` alongside `detectedLanguage` on each utterance. A destination change must not merge differently translated text into one display bubble, including after reopening the meeting.
- Ruling: retain the user's input preference while showing Qwen's effective `auto` value; backend session parsing also enforces this capability.
- Task 1 complete: policy tests observed missing-module/missing-implementation failures, then passed (9 frontend policy/reload tests; 20 Python policy cases).
- Task 2 complete: provider, multilingual, detected-language, snapshot, and destination regressions observed failing before changes. Full backend suite: 297 passed, 3 existing Pydantic deprecation warnings.
- Task 3 complete: full frontend suite 84 passed; TypeScript validation passed. Browser checks passed migration, independent settings, persistence, Qwen switching, real microphone-buffer draining, partial translation direction, stale-target rejection, mixed-language bubbles, and 390/768/1024/1280-pixel layouts.
- Browser tests control API/WebSocket results and use a synthetic browser microphone. They verify application behavior, not live model recognition accuracy.
- Final review: one independent reviewer found two Important issues, no Critical or Minor issues.
- Final fix 1: manual retranslations now update `targetLanguage` when starting and completing. A real-browser regression first reproduced a mixed-destination merged bubble, then passed with distinct bubbles.
- Final fix 2: pending translation direction is stored separately from the detection badge. A real-browser regression first reproduced an older Korean final completion discarding a valid English partial translation, then passed with the current translation and English badge preserved.
- Final verification after those fixes: 86 frontend tests, 304 backend tests, TypeScript, Prettier checks for the edited TypeScript files, and all seven browser-check groups passed. Production `npm run build` passed, including lint, type validation, and static export.
- Existing warnings: Pydantic class-based config deprecation and stale Browserslist data. No dependency upgrade is part of this change.
- Review scope rulings: live AWS acceptance/accuracy was not measured; within-result word-level code switching is not guaranteed; pre-existing manual-translation persistence is unchanged. Production build validation is complete.
- No infrastructure deployment, live model invocation, commit, or publication was performed. The language changes remain reviewable in the shared working tree.
- Follow-up UI feedback: removed the fixed labels above the selects; use `입력 · …` / `번역 · …` within the menus and align both controls and detection status on one row. Accessible names remain intact. Geometry checks at 320/360/390/768/1024/1280 pixels found a 0px difference between their vertical centers.
- Follow-up verification: all seven browser-check groups passed with the inline controls; desktop/mobile screenshots were visually inspected, and the production build (lint, types, static export) passed again.

## Production Deployment — 2026-10-28

The user's subsequent request, “배포도해”, authorized deploying the backend and frontend.

- Site: `https://transmeet.hi-yoo.com`.
- Release snapshot and rollback files: `/tmp/transmeet-deploy-speech-20261028T150347Z`.
- The frozen backend snapshot passed all 314 tests. Its cross-service model-list test used a copy of `agent/runtime/model.py` as a test fixture; the backend image still contains only the backend application and dependencies.
- ECR tag: `speech-languages-20261028-150347`.
- Deployed image digest: `sha256:5919a569e20207a826d17aed3ac785044e8dc0374fa1975666a1457d25f47393`.
- ECS service: `TransmeetStack-WsServiceF27E424E-6Sv7kqQAPFMC`, cluster `transmeet-ws-cluster`.
- Task definition: `TransmeetStackWsTaskDef7E775A03:23`, replacing revision `22`. The new revision preserves the previous runtime settings and pins the image by digest.
- ECS rollout completed with one running task and no pending tasks; the new target passed ALB health checks.
- Uploaded 550 frontend build files; previous hashed assets remain available. Backed up all 17 overwritten mutable files before publishing. HTML and service-worker files use `no-cache`.
- CloudFront invalidation `I767P9UHD79127OAD4WJUJ5BWX` completed on distribution `EZT7NX9EZJO9J`.
- Live checks at `2026-10-28T15:20:14Z`: published HTML, application bundle, and service-worker SHA-256 hashes matched the release; the login screen loaded without browser or asset errors; unauthenticated WebSocket requests were rejected with the expected `4401` close code.
- A normal browser registered the service worker and began installation without errors. Initial installation was still in progress within the 60-second observation window; offline readiness was not asserted.
- No live speech-recognition accuracy measurement was performed. The retained SageMaker endpoints and their scaling configuration were not changed.
- For a future full CDK deployment, include `-c wsImageTag=speech-languages-20261028-150347` to retain this backend release. The previous ECS task definition and frontend mutable-file backup are recorded in the release directory for rollback.
