# TransMeet theme presets implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement the approved design task by task.

**Goal:** Make Paper Atelier, Midnight Studio, Cobalt Grid and Clear Focus selectable application themes.

**Architecture:** Add a validated `preset` preference to the existing theme store and first-paint bootstrap. Semantic CSS tokens define each preset's surfaces, typography, corners, borders, sidebar and controls. Existing components consume those tokens, with small hooks for transcript and summary styling.

**Tech stack:** Next.js 14, React 18, TypeScript, Tailwind CSS 3, Node tests, Python Playwright.

**Spec:** The four approved PNG studies in the repository and the user's instruction “soft sage는 빼고 다 반영해줘”. `theme-preview-notes.md` describes the designs; Soft Sage is excluded.

## Constraints and decisions

- Keep the existing appearance as `default` for older saved settings; add exactly the four approved presets.
- Selecting a preset applies its recommended light/dark mode, theme palette and default font. Keep the user's scale, line spacing and conversation-size setting.
- Manual mode, accent and font controls remain available. “테마 기본” returns the accent/font to the selected preset.
- All presets support explicit light and dark modes and system mode. Midnight initially selects dark; the other approved presets initially select light.
- Preserve the existing retired-font migration, malformed-storage fallback, cross-tab updates and storage-denied behavior.
- Add a Korean serif face for Paper's default headings and source transcript; custom font choices override it.
- Keep the responsive information layout, accessible controls and actual meeting behavior.
- Work in the supplied checkout with scoped frontend changes; unrelated existing backend modifications and deleted experiment artifacts belong to the user.
- No source changes or tokens for a Soft Sage preset. Existing accent customization is separate from the rejected full theme.

## Review focus

- Reload, initial paint and another tab must agree on the same preset.
- Manual font/size choices must remain usable after selecting a preset.
- Colored sidebars need readable search, menus, selected meetings and destructive actions.
- Dark/light toggles must not leave light panels or unreadable accents behind.
- At 320/375px and 120% scale, theme choices, dialogs and recording controls must remain reachable.

## Tasks

### 1. Preference behavior

**Files:** `src/lib/theme.ts`, `src/components/ThemeProvider.tsx`, `tests/theme.test.mjs`.

- [x] Add regression tests for old settings, approved/invalid presets, recommended defaults, preserved reading preferences and bootstrap/runtime parity.
- [x] Run `npm run test:theme`; confirm the new behavior fails first.
- [x] Add `preset`, the `theme` accent, preset metadata and `createPresetTheme(current, preset)`.
- [x] Wire `selectPreset` through the provider's current-value reference.
- [x] Run `npm run test:theme`.

### 2. Preset styles and selection

**Files:** new `src/app/theme-presets.css`, new `src/components/ThemePresetPicker.tsx`; update global CSS, theme settings, layout fonts, Tailwind prose tokens and relevant common UI components.

- [x] Add four preview cards, a basic-style return control and the theme-default accent choice.
- [x] Add semantic shape/font/sidebar tokens and all four styles, including dark/light variants.
- [x] Apply small named styling hooks to conversations, headings, summary, recording controls and sidebar entries.
- [x] Keep custom reading sizes and status/error semantics intact.
- [x] Run TypeScript, lint and the existing UI tests.

### 3. Actual application verification

**Files:** new `tests/theme_browser.py`, `package.json`; font cache mapping in the documentation capture helper.

- [x] Build the actual app; render it locally using the existing guide fixtures and intercepted services.
- [x] Exercise preset selection, custom controls, reset, reload, keyboard operation, cross-tab storage and unavailable storage.
- [x] Inspect desktop/mobile screenshots, each color mode, large reading sizes and colored-sidebar menus.
- [x] Run `npm run test:ui`, `npm run test:theme:browser`, lint and a production build.
- [x] Obtain one independent review of the scoped changes and fix material findings.

## Progress

- Baseline: `npm run test:ui` — 78 passed, 0 failed.
- Task 1: complete — six new cases failed first, then all 14 theme tests passed.
- Task 2: complete — picker, four skins, common tokens and component hooks implemented. Type checking, lint and all 84 UI tests passed.
- Independent review: three important sidebar issues were reproduced in browser tests and fixed: active-control hover, title-editor contrast with custom accents, and keyboard focus on colored backgrounds. All three regression checks passed.
- Mobile visual inspection found overlapping tab labels at 120% scale. The regression check failed before adding wrapping and hiding small-screen settings icons; both 320px and 375px checks then passed.
- CSS integration: use plain CSS for the preset stylesheet because the project's Next/PostCSS loader processes CSS imports separately from Tailwind's layer directives.
- Font service: one production build hit an intermittent Google Fonts URL-format error in the existing Next font loader; a retry succeeded. The final production build including the mobile changes also succeeded.
- Review scope: author inspected fidelity to the four approved previews and mobile screenshots. Backend/live recording behavior was kept outside this presentation change; real application UI was exercised against intercepted guide fixtures.
- Task 3: complete — final combined browser run passed all four presets, both modes, keyboard/customization/reload/reset/cross-tab behavior, storage denial, 320px/375px at 120%, and the sidebar regression checks. No browser errors or external service requests. Artifacts are under `.playwright-mcp/theme-presets/`.
- User has already approved the PNG directions and requested implementation; proceed with the plan in this session.
