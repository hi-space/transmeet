# Blue Slate and Navy Note implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for implementation and one scoped independent review.

**Goal:** Replace Cobalt Grid with the two approved blue themes, then commit, push and deploy the frontend.

**Spec:** `theme-cobalt-01-blue-slate.png`, `theme-cobalt-02-navy-note.png`; user approved both. Earlier instructions authorize the commit/push/deploy workflow.

**Architecture:** Reuse the current preset store, CSS variables, chooser and release pipeline. Introduce `slate` and `navy`; migrate saved `cobalt` to `slate` in normalization and first-paint bootstrap. Shared blue palette plus separate card/header/transcript styles; explicit dark variants.

## Constraints

- Keep Paper Atelier, Midnight Studio, Clear Focus and the basic style.
- Keep custom accent/font/mode/scale/spacing when migrating an existing Cobalt setting.
- Selecting either new preset uses its recommended light mode and theme-native palette/font while preserving personal reading sizes.
- Navy's dark header must keep controls, focus and mobile quick settings readable.
- Personal font sizes and line spacing must override the prototype's compact reading styles.
- Preview all five presets at 320px and 375px, including 120% scale.
- Scope changes and release files to this theme work; preserve pre-existing backend and experiment changes.

## Review focus

- Migration agrees before React, after reload and across tabs, including storage denial.
- Switching away from Navy fully removes its header styling.
- Header and sidebar hover/focus/editor states remain readable in both modes and with custom accents.
- Large transcript size and custom spacing remain effective.
- New choice cards fit mobile screens without overlapping labels.

## Tasks

- [x] Add failing preset and legacy migration cases; implement the store/bootstrap update.
- [x] Replace Cobalt styles with Blue Slate and Navy Note, dark variants and preview cards.
- [x] Extend browser coverage for five presets, migration and Navy header controls.
- [x] Run UI tests, formatting/type/build checks and browser validation; inspect screenshots.
- [x] Obtain independent review, fix material findings and rerun relevant checks.
- [ ] Commit/push the scoped change, deploy the verified static export and verify production assets.

## Verification

- Five preset/migration cases failed before implementation; all 16 theme tests passed afterward.
- All 86 UI tests passed. Production build, type checking, lint and scoped formatting checks passed.
- Browser suite passed all five presets in both modes, desktop/mobile/120% scale, custom reading controls, reload/reset/storage denial, cross-tab legacy migration and Navy header focus/quick settings/switch-away behavior.
- Independent review found no important issues. Its heading-size specificity finding was corrected to match the approved design.
- Large-reading assertions wait for the actual computed size after React updates, preventing premature checks of the prior compact size.
- Release uses the verified `out/` export with the existing S3/CloudFront process; the final release receipt records commit, file hashes and invalidation completion.
