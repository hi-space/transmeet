import assert from 'node:assert/strict'
import { test } from 'node:test'
import vm from 'node:vm'
import {
  createDefaultTheme,
  normalizeThemeSettings,
  resolveThemeMode,
  THEME_BOOTSTRAP_SCRIPT,
} from '../src/lib/theme.ts'

test('invalid saved fields fall back independently while valid preferences survive', () => {
  assert.deepEqual(
    normalizeThemeSettings({
      mode: 'dark',
      accent: 'amazon',
      font: 'removed-font',
      scale: -100,
      lineSpacing: null,
      extra: 'ignored',
    }),
    {
      ...createDefaultTheme(),
      mode: 'dark',
      accent: 'amazon',
    }
  )
})

test('non-object and prototype-like preferences cannot become theme attributes', () => {
  for (const input of [null, false, 12, 'dark', [], { accent: '__proto__', font: 'constructor' }]) {
    assert.deepEqual(normalizeThemeSettings(input), createDefaultTheme())
  }
})

test('explicit color mode takes priority over the system setting', () => {
  assert.equal(resolveThemeMode('light', true), 'light')
  assert.equal(resolveThemeMode('dark', false), 'dark')
  assert.equal(resolveThemeMode('system', true), 'dark')
  assert.equal(resolveThemeMode('system', false), 'light')
})

test('replaces the retired font while preserving the rest of a saved theme', () => {
  const saved = Object.freeze({
    mode: 'dark',
    accent: 'amazon',
    font: 'serif',
    scale: 120,
    lineSpacing: 'relaxed',
  })
  assert.deepEqual(normalizeThemeSettings(saved), { ...saved, font: 'plex' })
  assert.equal(saved.font, 'serif')
})

function bootstrap({ saved = null, prefersDark = false, denied = false } = {}) {
  const classes = new Set()
  const root = {
    dataset: {},
    style: {},
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name)
        else classes.delete(name)
      },
    },
  }
  vm.runInNewContext(THEME_BOOTSTRAP_SCRIPT, {
    document: { documentElement: root },
    window: { matchMedia: () => ({ matches: prefersDark }) },
    localStorage: {
      getItem() {
        if (denied) throw new Error('Storage access denied')
        return saved
      },
    },
  })
  return { root, dark: classes.has('dark') }
}

test('saved appearance is applied by the bootstrap before React starts', () => {
  const { root, dark } = bootstrap({
    saved: JSON.stringify({
      mode: 'light',
      accent: 'amazon',
      font: 'plex',
      scale: 120,
      lineSpacing: 'relaxed',
    }),
    prefersDark: true,
  })
  assert.equal(dark, false)
  assert.equal(root.style.colorScheme, 'light')
  assert.equal(root.dataset.accent, 'amazon')
  assert.equal(root.dataset.font, 'plex')
  assert.equal(root.dataset.scale, '120')
  assert.equal(root.dataset.lineSpacing, 'relaxed')
})

test('the bootstrap replaces a saved retired font before the first paint', () => {
  const { root, dark } = bootstrap({
    saved: '{"mode":"dark","accent":"amazon","font":"serif","scale":110,"lineSpacing":"compact"}',
  })
  assert.equal(dark, true)
  assert.equal(root.dataset.font, 'plex')
  assert.equal(root.dataset.accent, 'amazon')
  assert.equal(root.dataset.scale, '110')
  assert.equal(root.dataset.lineSpacing, 'compact')
})

test('corrupt or unavailable storage still applies the system color mode', () => {
  for (const options of [{ saved: '{broken' }, { saved: 'null' }, { denied: true }]) {
    const { root, dark } = bootstrap({ ...options, prefersDark: true })
    assert.equal(dark, true)
    assert.equal(root.style.colorScheme, 'dark')
    assert.equal(root.dataset.accent, createDefaultTheme().accent)
  }
})

test('bootstrap rejects invalid fields without losing the saved accent', () => {
  const { root, dark } = bootstrap({
    saved: '{"accent":"amazon","mode":"unknown","scale":10000,"font":"__proto__"}',
    prefersDark: true,
  })
  assert.equal(dark, true)
  assert.equal(root.dataset.accent, 'amazon')
  assert.equal(root.dataset.scale, String(createDefaultTheme().scale))
  assert.equal(root.dataset.font, createDefaultTheme().font)
})
