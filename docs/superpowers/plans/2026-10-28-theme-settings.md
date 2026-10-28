# Theme Settings Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement and verify each task in this workspace.

**Goal:** 설정에서 색상, 글꼴, 크기와 줄 간격을 변경하고 다음 방문에도 유지한다. Amazon 오렌지를 선택하면 앱 전체 포인트 색상이 바뀐다.

**Architecture:** Extend the root ThemeProvider with a shared context and a separate, validated `transmeet-theme` localStorage entry. CSS variables supply the accent palette, primary button contrast, fonts, scale and reading line height. A small bootstrap script applies saved preferences before hydration.

**Tech Stack:** Existing Next.js 14, React 18, Tailwind CSS 3 and TypeScript. Use built-in Node tests and browser verification; no additional runtime dependencies.

**Spec:** User requests in this conversation: settings-based color/font/size customization, specifically an Amazon orange choice alongside the existing blue.

## Global Constraints

- Keep existing cyan and system color mode as defaults.
- Provide cyan, Amazon orange (`#FF9900`), blue, violet, emerald and rose.
- Keep recording/error colors semantic; recolor app accents, including Markdown headings and glass borders.
- Offer default, Korean sans, Korean serif and monospace fonts.
- Support 90%, 100%, 110% and 120% UI scale, reading line spacing and the existing voice text size control.
- Apply changes immediately and persist locally; reset appearance without resetting translation/audio preferences.
- Preserve static export, mobile layouts and existing functionality.
- Work locally; no deployment or changes to existing untracked browser artifacts.

## Review Focus

- Malformed or obsolete saved preferences must fall back independently, without breaking the page.
- An explicit light/dark selection must survive reload and override system changes.
- Orange filled controls must use a readable foreground; subtle accents need darker shades on light backgrounds.
- The settings sheet must remain usable at 120% on a narrow phone, including keyboard navigation and closing.
- The first rendered theme, header toggle and settings controls must agree, including when storage is unavailable.

## Task 1: Theme preferences and persistence

**Files:** `src/lib/theme.ts`, `src/components/ThemeProvider.tsx`, `src/app/layout.tsx`, `src/components/Header.tsx`, `tests/theme.test.mjs`, `package.json`.

**Interfaces:** `ThemeSettings`, `createDefaultTheme()`, `normalizeThemeSettings(unknown)`, `resolveThemeMode(mode, prefersDark)`, `THEME_BOOTSTRAP_SCRIPT`, `useTheme()` with `theme`, `isDark`, `updateTheme`, `resetTheme`.

- [x] Add regression tests for invalid stored values, explicit/system modes, bootstrap application and denied storage; observe failure before implementation.
- [x] Implement validated defaults and a shared theme provider; persist user updates and synchronize tabs.
- [x] Apply saved attributes before hydration and connect the existing header toggle to context.
- [x] Verify unit tests and TypeScript.

## Task 2: Color and typography integration

**Files:** `tailwind.config.ts`, `src/app/globals.css`, existing app components, `src/app/layout.tsx`.

- [x] Introduce semantic accent shades and primary background/foreground colors.
- [x] Migrate cyan accents and hard-coded glass tints, preserving status colors.
- [x] Load Korean font choices through the existing Next font pipeline, expose scale and reading line height.
- [x] Verify orange controls and typography in the actual browser.

## Task 3: Settings interface and verification

**Files:** `src/components/ThemeSettings.tsx`, `src/components/SettingsPanel.tsx`, `README.md`.

- [x] Add theme/general tabs, labeled choices, a live conversation preview and appearance reset.
- [x] Make the modal scrollable with a persistent header and keyboard focus handling.
- [x] Connect the existing voice text sizes without duplicating their stored state.
- [x] Verify immediate updates, reload, header synchronization, reset, unavailable storage, and light/dark desktop/mobile layouts.
- [x] Run lint, TypeScript, theme tests and production static export; review the final diff.

## Verification results

- `npm run test:theme`: 6/6 passed.
- `npx tsc --noEmit`: passed.
- `npm run lint` and `npm run build`: passed; one existing `react-hooks/exhaustive-deps` warning in `src/app/page.tsx` about `settings.translationTiming`. Production static export generated all four pages.
- Browser: all six primary color palettes and all four font families apply; primary button text contrast ranges from 4.70:1 to 6.34:1.
- Browser: reload persistence, header/settings synchronization, system versus explicit modes, appearance-only reset, keyboard focus trapping/restoration, denied writes and cross-tab synchronization passed.
- Responsive checks: 320, 360, 400, 414, 640, 768 and 1280px layouts at 120% scale; 667×375 landscape and reduced motion passed.
- Final review fixes: responsive font utilities no longer override reading line spacing; meeting drawer width is capped to fit narrow screens. Both were reproduced and verified in the browser.
