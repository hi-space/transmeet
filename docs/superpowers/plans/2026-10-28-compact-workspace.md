# Compact Workspace Implementation Plan

> **For agentic workers:** Implement this plan inline with superpowers:executing-plans. Track the tasks below and verify the resulting interface in a browser.

**Goal:** Make TransMeet compact, visually coherent, and immediately usable without adding promotional copy or onboarding sections.

**Architecture:** Retain the existing Next.js application, theme preferences, and meeting/audio handlers. Consolidate the workspace into a meeting sidebar, a generous conversation area, and one tabbed utility panel. Share surface, control, tab, icon, and dialog conventions across the application.

**Tech Stack:** Next.js 14, React 18, TypeScript, Tailwind CSS 3, existing theme CSS variables.

**Spec:** The user's request in this session: consistent UX, improved visual design, compact layout, intuitive controls, and no unnecessary introductions or explanations.

## Global Constraints

- Keep existing translation, audio, meeting, summary, and page-generation capabilities.
- Preserve saved theme and settings values.
- Use short Korean action labels consistently on desktop and mobile.
- Use existing dependencies; avoid decorative assets and extra product copy.
- Keep live account data and infrastructure outside local visual verification.
- Implement in the current shared workspace; retain the existing untracked `.playwright-mcp/` directory.
- Keep changes reviewable in the working tree without committing or deploying.

## Review Focus

- At tablet widths, only one version of the active content is visible.
- Korean composition events must not submit unfinished text on Enter.
- Message text can be selected without triggering translation.
- Hidden mobile navigation is not reachable by keyboard; dialogs restore focus.
- Long meeting titles, large saved text sizes, and dark themes remain usable.

## Task 1: Shared visual foundation

**Files:** `src/app/globals.css`, `src/components/ui/Icon.tsx`, `src/components/ui/Dialog.tsx`, `src/components/ui/Tabs.tsx`, `src/components/ui/Switch.tsx`

**Interfaces:** Named SVG icons; tabs with labels and keyboard navigation; a modal dialog with native focus handling; shared `ui-*` control and surface classes.

- [x] Establish neutral surfaces, clear borders, compact spacing, and readable muted text in both themes.
- [x] Add shared icons and keyboard-operable tabs without a dependency.
- [x] Unify modal appearance and focus behavior.
- [x] Respect reduced motion and touch target sizes.

## Task 2: Workspace and navigation

**Files:** `src/app/page.tsx`, `src/components/Header.tsx`, `src/components/MeetingSidebar.tsx`, `src/components/MobileTabBar.tsx`, `src/hooks/useSummaryResize.ts`

**Interfaces:** Existing meeting callbacks remain intact. The right utility panel supports `notes | summary | page`; mobile uses the same names and destinations.

- [x] Reduce the sidebar width, add a visible new-meeting action and meeting search.
- [x] Use one right panel with tabs; retain an option to collapse it.
- [x] Align headers and panels, and place the composer within the workspace.
- [x] Keep mobile drawer and desktop content mutually exclusive at the breakpoint.
- [x] Verify meeting selection, search, rename, cancellation, tabs, and responsive behavior.

## Task 3: Readable conversation and clear actions

**Files:** `src/components/VoiceArea.tsx`, `src/components/NotesArea.tsx`, `src/components/ControlPanel.tsx`, `src/components/SummaryPanel.tsx`, `src/components/MeetingPagePanel.tsx`, `src/components/EditableTitle.tsx`, `src/components/MeetingActions.tsx`, `src/lib/workspace.ts`, `tests/workspace.test.mjs`

- [x] Make transcript cards readable without dimming earlier messages.
- [x] Replace implicit card-click translation with an explicit action.
- [x] Unify playback, regeneration, copy, recording, and send controls.
- [x] Keep empty and loading states concise and useful.
- [x] Preserve Korean IME composition on Enter.
- [x] Verify text selection, input, summary copying, empty meetings, and panel resizing.

## Task 4: Settings, quick translation, and sign-in

**Files:** `src/components/SettingsPanel.tsx`, `src/components/ThemeSettings.tsx`, `src/components/QuickTranslatePopup.tsx`, `src/components/AuthScreen.tsx`, `src/components/EmailNotifySetting.tsx`

- [x] Apply shared dialog and form styles.
- [x] Group advanced settings under a disclosure without removing options.
- [x] Make labels, selected states, focus, and touch interaction consistent.
- [x] Verify quick translation errors, keyboard dismissal, focus restoration, and theme persistence.

## Task 5: Verification and review

- [x] Run `npm run test:ui`, `npm run lint`, TypeScript checking, and `npm run build`.
- [x] Inspect browser screenshots at mobile, tablet, and desktop widths in light and dark mode.
- [x] Exercise long titles, large text settings, keyboard tabs, and modal focus.
- [x] Review the final diff and resolve material regressions.

## Initial evidence

- Existing settings tests: 11 passed.
- Existing desktop and mobile screenshots saved under `/tmp/transmeet-ux/`.
- Baseline findings: mixed header/control sizes, hover-only actions, whole-card translation, a tablet breakpoint mismatch, and separate utility columns competing with the conversation.


## Final verification

- `npm run test:ui`: 14 passed, including mobile-tab priority regression tests.
- `npm run lint`: no warnings or errors.
- `npx tsc --noEmit --pretty false`: passed; the final production build also passed type checking.
- `npm run build`: passed; generated only the main application and not-found route.
- `git diff --check`: passed.
- Browser workflow checks: 18 passed across search, meeting actions, title editing, tabs, resizing, composition, dialogs, history, theme persistence, and responsive layout.
- Review regression checks: 4 passed for background summary updates, hidden-panel scrolling, large-text utility tabs, and initial translation focus.
- Additional fixtures verified login form validation, password confirmation errors, streamed translation display/copy/history, and meeting-end control layout at 320–1024px with 120% scale.
- Final screenshots: `.playwright-mcp/ux-redesign/`.

## Review decisions and scope

- A background summary updates the desktop utility but preserves the mobile reader's selected tab. This retains the previous mobile behavior while allowing one shared set of panels.
- Transcript and notes scrolling now observes visibility so selecting a long meeting while its panel is hidden still opens at the latest content.
- Desktop utility tabs use concise text labels; selected tabs have a visible shape in both themes.
- Native dialogs provide background inertness and Escape handling; explicit initial focus and Tab wrapping keep keyboard operation consistent.
- Browser checks used local mock data and a temporary UI fixture. Actual account authentication, cloud audio/TTS, email delivery, and publishing were not invoked. The fixture route was removed before the production build.
- Concurrent backend, WebSocket, and automatic-page work from another session was preserved. Only related visible UI labels in the shared README were adjusted.
- The build recovered from transient Google Fonts download retries and emitted the existing Browserslist data-age notice; no dependency updates were required for this redesign.

## Follow-up: contextual controls and resizable sidebar

Requested refinements:

- Keep recording download beside recording start/stop and meeting end.
- Put the message composer inside the notes panel, where its output appears.
- Allow dragging the meeting sidebar to change its width.

Implemented:

- A compact recording control group with inline download and conditional status feedback.
- `NotesComposer` inside the notes panel. The draft remains in page state across tab changes; Enter sends, Shift+Enter inserts a line, and IME composition is preserved.
- Summary completion updates content without navigating away from a draft. Explicit summary requests reveal the summary when requested, before asynchronous work starts.
- Empty notes can shrink and scroll so the composer stays visible on short screens.
- Shared `usePanelResize` for both panel edges. The meeting list supports 200–420 px, starts at 240 px, and stores `transmeet-sidebar-width`. The existing right-panel range, storage key, and drag direction are preserved.
- Keyboard resizing, collapse/expand restoration, reload persistence, and a fixed mobile drawer.

Verification before deployment: 22 existing tests, TypeScript, lint, and 15 browser checks passed. Browser checks include pointer/keyboard resize, stored widths, mobile layout, draft retention, real component translation flow with a local API fixture, short-screen composition, and background summary focus retention. Root preview PNGs were refreshed.


## Follow-up: floating controls and a compact meeting heading

The user requested reclaiming the entire bottom control row and keeping the title, date, and conversation count on one line.

- Recording now uses an absolute floating microphone control. Meeting end and recording download live in its disclosure menu; Escape, outside click, and focus restoration are supported.
- The dedicated footer row is removed. Conversation content fills the reclaimed space, with clearance at the end of scrollable content for the floating buttons.
- The meeting heading is one 48px row at the normal scale. Mobile uses a shorter date and language selector; editing temporarily hides metadata to give the title input room.
- The controls measure the current mobile editor or published-page action area with ResizeObserver. Recording controls avoid the notes composer and both normal and expanded page-footer actions.
- Existing engine readiness labels, recording-disabled conditions, and status content from concurrent STT work are preserved.
- Current verification: 26 unit tests passed, browser checks cover all three prior refinements plus floating-menu keyboard behavior, title editing, compact heading, mobile/landscape positioning, and published-page button hit targets. The local API fixture preserves requested meeting IDs when returning meeting details.

## Placement clarification

The user clarified that the floating recorder belongs at the **conversation panel's** lower-right corner, not at the right edge of the entire workspace. Its anchor now measures that panel relative to the workspace and follows panel resizing or collapse. On mobile, the visible content panel is used and editor/page-action clearance remains active. Desktop notes and summary panels no longer reserve space for a recorder that is in the conversation panel.
