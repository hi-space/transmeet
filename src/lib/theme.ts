export const THEME_STORAGE_KEY = 'transmeet-theme'

export const THEME_VALUES = {
  preset: ['default', 'paper', 'midnight', 'slate', 'navy', 'clear'],
  mode: ['system', 'light', 'dark'],
  accent: ['theme', 'cyan', 'amazon', 'blue', 'violet', 'emerald', 'rose'],
  font: ['default', 'sans', 'plex', 'mono'],
  scale: [90, 100, 110, 120],
  lineSpacing: ['compact', 'normal', 'relaxed'],
} as const

const FONT_REPLACEMENT = { from: 'serif', to: 'plex' } as const
const PRESET_REPLACEMENT = { from: 'cobalt', to: 'slate' } as const

export type ThemeSettings = {
  [K in keyof typeof THEME_VALUES]: (typeof THEME_VALUES)[K][number]
}

export const THEME_PRESETS = [
  {
    value: 'paper',
    label: 'Paper Atelier',
    description: '종이색 · 명조 · 문서형 대화',
    mode: 'light',
  },
  {
    value: 'midnight',
    label: 'Midnight Studio',
    description: '차콜 · 민트 · 스튜디오',
    mode: 'dark',
  },
  {
    value: 'slate',
    label: 'Blue Slate',
    description: '블루그레이 · 단정한 카드',
    mode: 'light',
  },
  {
    value: 'navy',
    label: 'Navy Note',
    description: '네이비 상단바 · 문서형 대화',
    mode: 'light',
  },
  {
    value: 'clear',
    label: 'Clear Focus',
    description: '큰 본문 · 진한 글자 · 오렌지',
    mode: 'light',
  },
] as const

/** New visits and the appearance reset use this same starting point. */
export function createDefaultTheme(): ThemeSettings {
  // 기본 조합을 바꿔도 이미 저장된 사용자 선택은 유지됩니다.
  return {
    preset: 'default',
    mode: 'system',
    accent: 'cyan',
    font: 'default',
    scale: 100,
    lineSpacing: 'normal',
  }
}

/** A complete look changes together; personal reading sizes remain independent. */
export function createPresetTheme(
  current: ThemeSettings,
  preset: ThemeSettings['preset']
): ThemeSettings {
  const selected = THEME_PRESETS.find((option) => option.value === preset)
  const defaults = createDefaultTheme()
  return {
    ...current,
    preset: selected?.value ?? defaults.preset,
    mode: selected?.mode ?? defaults.mode,
    accent: selected ? 'theme' : defaults.accent,
    font: 'default',
  }
}

/** Treat saved preferences as untrusted: obsolete fields fall back independently. */
export function normalizeThemeSettings(value: unknown): ThemeSettings {
  const defaults = createDefaultTheme()
  if (!value || typeof value !== 'object' || Array.isArray(value)) return defaults

  const saved = { ...(value as Record<string, unknown>) }
  if (saved.font === FONT_REPLACEMENT.from) saved.font = FONT_REPLACEMENT.to
  if (saved.preset === PRESET_REPLACEMENT.from) saved.preset = PRESET_REPLACEMENT.to
  return Object.fromEntries(
    Object.entries(THEME_VALUES).map(([key, allowed]) => [
      key,
      (allowed as readonly unknown[]).includes(saved[key])
        ? saved[key]
        : defaults[key as keyof ThemeSettings],
    ])
  ) as ThemeSettings
}

export function resolveThemeMode(
  mode: ThemeSettings['mode'],
  prefersDark: boolean
): 'light' | 'dark' {
  return mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode
}

export function applyTheme(theme: ThemeSettings, prefersDark: boolean) {
  const root = document.documentElement
  const mode = resolveThemeMode(theme.mode, prefersDark)
  root.classList.toggle('dark', mode === 'dark')
  root.style.colorScheme = mode
  root.dataset.themeMode = theme.mode
  root.dataset.preset = theme.preset
  root.dataset.accent = theme.accent
  root.dataset.font = theme.font
  root.dataset.scale = String(theme.scale)
  root.dataset.lineSpacing = theme.lineSpacing
}

// The static export cannot read browser storage on the server. Apply only trusted,
// allow-listed attributes before the first paint, using the same values as React.
export const THEME_BOOTSTRAP_SCRIPT = `
(function () {
  var defaults = ${JSON.stringify(createDefaultTheme())};
  var allowed = ${JSON.stringify(THEME_VALUES)};
  var fontReplacement = ${JSON.stringify(FONT_REPLACEMENT)};
  var presetReplacement = ${JSON.stringify(PRESET_REPLACEMENT)};
  var saved = {};
  try {
    var parsed = JSON.parse(localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)}) || 'null');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed;
  } catch (_) {}
  if (saved.font === fontReplacement.from) saved.font = fontReplacement.to;
  if (saved.preset === presetReplacement.from) saved.preset = presetReplacement.to;
  var theme = {};
  Object.keys(defaults).forEach(function (key) {
    theme[key] = allowed[key].indexOf(saved[key]) !== -1 ? saved[key] : defaults[key];
  });
  var dark = theme.mode === 'dark' ||
    (theme.mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  var root = document.documentElement;
  root.classList.toggle('dark', dark);
  root.style.colorScheme = dark ? 'dark' : 'light';
  root.dataset.themeMode = theme.mode;
  ['preset', 'accent', 'font', 'scale', 'lineSpacing'].forEach(function (key) {
    root.dataset[key] = String(theme[key]);
  });
})();
`
