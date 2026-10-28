'use client'

import { useEffect, useState } from 'react'
import type { WsStatus } from '@/lib/websocket'
import { useTheme } from '@/components/ThemeProvider'
import BrandLogo from '@/components/BrandLogo'
import Dialog from '@/components/ui/Dialog'
import Icon from '@/components/ui/Icon'
import SpeechLanguageControls from '@/components/SpeechLanguageControls'
import type { SourceLanguage, TranslationTarget } from '@/lib/speechLanguages'
import type { SttProvider } from '@/lib/sttProviders'

interface Props {
  isRecording: boolean
  wsStatus?: WsStatus
  onToggleSidebar: () => void
  sidebarOpen?: boolean
  onToggleQuickTranslate: () => void
  quickTranslateOpen: boolean
  onToggleSettings: () => void
  settingsOpen?: boolean
  onLogout?: () => void
  sttProvider: SttProvider
  sttStreaming?: boolean
  sourceLang: SourceLanguage
  translationTarget: TranslationTarget
  onChangeSourceLang: (source: SourceLanguage) => void
  onChangeTranslationTarget: (target: TranslationTarget) => void
  detectedLang?: 'en' | 'ko' | null
}

export default function Header({
  isRecording,
  wsStatus,
  onToggleSidebar,
  sidebarOpen,
  onToggleQuickTranslate,
  quickTranslateOpen,
  onToggleSettings,
  settingsOpen,
  onLogout,
  sttProvider,
  sttStreaming = false,
  sourceLang,
  translationTarget,
  onChangeSourceLang,
  onChangeTranslationTarget,
  detectedLang,
}: Props) {
  const { isDark, updateTheme } = useTheme()
  const [mobileSettingsOpen, setMobileSettingsOpen] = useState(false)

  useEffect(() => {
    if (!mobileSettingsOpen) return
    const desktop = window.matchMedia('(min-width: 1024px)')
    const closeOnDesktop = () => {
      if (desktop.matches) setMobileSettingsOpen(false)
    }
    closeOnDesktop()
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [mobileSettingsOpen])

  const status = isRecording
    ? { label: '녹음 중', color: 'bg-red-500', pulse: true }
    : wsStatus === 'connected'
      ? { label: '연결됨', color: 'bg-emerald-500', pulse: false }
      : wsStatus === 'connecting'
        ? { label: '연결 중', color: 'bg-amber-500', pulse: true }
        : wsStatus === 'error' || wsStatus === 'disconnected'
          ? { label: '연결 끊김', color: 'bg-red-500', pulse: false }
          : null

  return (
    <header className="relative z-20 flex h-12 shrink-0 items-center border-b border-subtle bg-panel px-2 lg:h-14 lg:px-4">
      <div className="flex min-w-0 items-center gap-2 lg:w-[14rem] lg:shrink-0">
        <button
          type="button"
          onClick={onToggleSidebar}
          className="ui-icon-button lg:hidden"
          aria-label="회의 목록 열기"
          aria-expanded={sidebarOpen}
        >
          <Icon name="panelLeft" className="h-[18px] w-[18px]" />
        </button>
        <BrandLogo />
      </div>

      <div className="ml-4 hidden lg:block">
        <SpeechLanguageControls
          provider={sttProvider}
          streaming={sttStreaming}
          sourceLang={sourceLang}
          translationTarget={translationTarget}
          onChangeSourceLang={onChangeSourceLang}
          onChangeTranslationTarget={onChangeTranslationTarget}
          detectedLang={detectedLang}
        />
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-0.5 sm:gap-1">
        {status && (
          <div
            role="status"
            className="mr-2 hidden items-center gap-1.5 text-xs text-muted md:flex"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${status.color} ${status.pulse ? 'blink-dot' : ''}`}
            />
            {status.label}
          </div>
        )}
        <button
          type="button"
          onClick={onToggleQuickTranslate}
          aria-label="빠른 번역"
          aria-haspopup="dialog"
          aria-expanded={quickTranslateOpen}
          title="빠른 번역"
          className={`ui-button ${quickTranslateOpen ? 'ui-button-tinted' : ''} max-sm:px-2.5`}
        >
          <Icon name="languages" className="h-[18px] w-[18px]" />
          <span className="hidden md:inline">빠른 번역</span>
        </button>
        <span className="mx-1 hidden h-4 w-px bg-slate-200 dark:bg-slate-700 sm:block" />
        <button
          type="button"
          onClick={() => updateTheme({ mode: isDark ? 'light' : 'dark' })}
          aria-label={isDark ? '밝은 화면으로 전환' : '어두운 화면으로 전환'}
          title={isDark ? '밝은 화면으로 전환' : '어두운 화면으로 전환'}
          className="ui-icon-button hidden lg:inline-flex"
        >
          <Icon name={isDark ? 'sun' : 'moon'} className="h-[18px] w-[18px]" />
        </button>
        <button
          type="button"
          onClick={onToggleSettings}
          aria-label="설정"
          aria-haspopup="dialog"
          aria-expanded={settingsOpen}
          title="설정"
          className="ui-icon-button hidden lg:inline-flex"
        >
          <Icon name="settings" className="h-[18px] w-[18px]" />
        </button>
        {onLogout && (
          <button
            type="button"
            onClick={onLogout}
            aria-label="로그아웃"
            title="로그아웃"
            className="ui-icon-button hidden lg:inline-flex"
          >
            <Icon name="logout" />
          </button>
        )}
        <button
          type="button"
          onClick={() => setMobileSettingsOpen(true)}
          aria-label="설정"
          aria-haspopup="dialog"
          aria-expanded={mobileSettingsOpen}
          title="언어 및 화면 설정"
          className="ui-icon-button lg:hidden"
        >
          <Icon name="settings" className="h-[18px] w-[18px]" />
        </button>
      </div>
      {mobileSettingsOpen && (
        <Dialog labelledBy="mobile-settings-title" onClose={() => setMobileSettingsOpen(false)}>
          <div className="ui-panel-header">
            <h2 id="mobile-settings-title" className="text-sm font-semibold text-ink">
              빠른 설정
            </h2>
            <button
              type="button"
              onClick={() => setMobileSettingsOpen(false)}
              aria-label="빠른 설정 닫기"
              className="ui-icon-button"
            >
              <Icon name="close" />
            </button>
          </div>
          <div className="min-h-0 space-y-4 overflow-y-auto p-4">
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-ink">음성 및 번역 언어</h3>
              <SpeechLanguageControls
                compact
                provider={sttProvider}
                streaming={sttStreaming}
                sourceLang={sourceLang}
                translationTarget={translationTarget}
                onChangeSourceLang={onChangeSourceLang}
                onChangeTranslationTarget={onChangeTranslationTarget}
                detectedLang={detectedLang}
              />
            </div>
            <div className="flex flex-col gap-1">
              <button
                type="button"
                onClick={() => updateTheme({ mode: isDark ? 'light' : 'dark' })}
                className="ui-button min-h-11 justify-start"
              >
                <Icon name={isDark ? 'sun' : 'moon'} />
                {isDark ? '밝은 화면으로 전환' : '어두운 화면으로 전환'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setMobileSettingsOpen(false)
                  onToggleSettings()
                }}
                className="ui-button min-h-11 justify-start"
              >
                <Icon name="settings" />
                전체 설정
              </button>
              {onLogout && (
                <button
                  type="button"
                  onClick={() => {
                    setMobileSettingsOpen(false)
                    onLogout()
                  }}
                  className="ui-button min-h-11 justify-start"
                >
                  <Icon name="logout" />
                  로그아웃
                </button>
              )}
            </div>
          </div>
        </Dialog>
      )}
    </header>
  )
}
