import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import AuthScreen from '@/components/AuthScreen'
import SettingsPanel from '@/components/SettingsPanel'
import ThemeProvider from '@/components/ThemeProvider'
import { useSettings } from '@/hooks/useSettings'

function Fixture() {
  const { settings, updateSettings } = useSettings()
  const [open, setOpen] = useState(true)
  const [authenticated, setAuthenticated] = useState(false)
  const account = new URLSearchParams(location.search).has('account')
  if (authenticated) return <p>로그인 완료</p>
  return account ? (
    <>
      <button onClick={() => setOpen(true)}>설정 열기</button>
      {open && (
        <SettingsPanel
          settings={settings}
          onUpdate={updateSettings}
          onClose={() => setOpen(false)}
          summaryTemplates={{
            templates: [],
            save: async () => undefined,
            remove: async () => undefined,
          }}
        />
      )}
    </>
  ) : (
    <AuthScreen onAuth={() => setAuthenticated(true)} />
  )
}

createRoot(document.getElementById('root')!).render(
  <ThemeProvider>
    <Fixture />
  </ThemeProvider>
)
