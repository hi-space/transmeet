'use client'

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import {
  applyTheme,
  createDefaultTheme,
  createPresetTheme,
  normalizeThemeSettings,
  resolveThemeMode,
  THEME_STORAGE_KEY,
  type ThemeSettings,
} from '@/lib/theme'

interface ThemeContextValue {
  theme: ThemeSettings
  isDark: boolean
  storageUnavailable: boolean
  updateTheme: (patch: Partial<ThemeSettings>) => void
  selectPreset: (preset: ThemeSettings['preset']) => void
  resetTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readSavedTheme(): { theme: ThemeSettings; storageUnavailable: boolean } {
  let raw: string | null
  try {
    raw = localStorage.getItem(THEME_STORAGE_KEY)
  } catch {
    return { theme: createDefaultTheme(), storageUnavailable: true }
  }
  try {
    return { theme: normalizeThemeSettings(JSON.parse(raw || 'null')), storageUnavailable: false }
  } catch {
    return { theme: createDefaultTheme(), storageUnavailable: false }
  }
}

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<ThemeSettings>(createDefaultTheme)
  const [prefersDark, setPrefersDark] = useState(false)
  const [ready, setReady] = useState(false)
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const currentTheme = useRef(theme)

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const saved = readSavedTheme()
    currentTheme.current = saved.theme
    setTheme(saved.theme)
    setStorageUnavailable(saved.storageUnavailable)
    setPrefersDark(media.matches)
    setReady(true)

    const onSystemChange = (event: MediaQueryListEvent) => setPrefersDark(event.matches)
    const onStorageChange = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return
      const next = readSavedTheme()
      currentTheme.current = next.theme
      setTheme(next.theme)
      setStorageUnavailable(next.storageUnavailable)
    }
    media.addEventListener('change', onSystemChange)
    window.addEventListener('storage', onStorageChange)
    return () => {
      media.removeEventListener('change', onSystemChange)
      window.removeEventListener('storage', onStorageChange)
    }
  }, [])

  useEffect(() => {
    // Do not overwrite the bootstrap's saved theme with the initial SSR defaults.
    if (ready) applyTheme(theme, prefersDark)
  }, [theme, prefersDark, ready])

  const updateTheme = useCallback((patch: Partial<ThemeSettings>) => {
    const next = normalizeThemeSettings({ ...currentTheme.current, ...patch })
    currentTheme.current = next
    setTheme(next)
    try {
      localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(next))
      setStorageUnavailable(false)
    } catch {
      // Appearance remains usable for this visit when browser storage is unavailable.
      setStorageUnavailable(true)
    }
  }, [])

  const resetTheme = useCallback(() => updateTheme(createDefaultTheme()), [updateTheme])
  const selectPreset = useCallback(
    (preset: ThemeSettings['preset']) =>
      updateTheme(createPresetTheme(currentTheme.current, preset)),
    [updateTheme]
  )

  return (
    <ThemeContext.Provider
      value={{
        theme,
        isDark: resolveThemeMode(theme.mode, prefersDark) === 'dark',
        storageUnavailable,
        updateTheme,
        selectPreset,
        resetTheme,
      }}
    >
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}
