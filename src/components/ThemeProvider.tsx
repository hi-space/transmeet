'use client'

import { useEffect } from 'react'

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')

    const applyTheme = (dark: boolean) => {
      document.documentElement.classList.toggle('dark', dark)
    }

    applyTheme(mediaQuery.matches)

    const handler = (e: MediaQueryListEvent) => applyTheme(e.matches)
    mediaQuery.addEventListener('change', handler)
    return () => mediaQuery.removeEventListener('change', handler)
  }, [])

  return <>{children}</>
}
