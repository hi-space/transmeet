'use client'

import type { WsStatus } from '@/lib/websocket'
import { useTheme } from '@/components/ThemeProvider'
import BrandLogo from '@/components/BrandLogo'
import Icon from '@/components/ui/Icon'

type SourceLanguage = 'en' | 'ko' | 'auto'

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
  sourceLang: SourceLanguage
  onChangeLangDirection: (source: SourceLanguage) => void
  detectedLang?: 'en' | 'ko' | null
}

export function LanguageDirection({
  sourceLang,
  onChange,
  detectedLang,
  compact = false,
}: {
  sourceLang: SourceLanguage
  onChange: (source: SourceLanguage) => void
  detectedLang?: 'en' | 'ko' | null
  compact?: boolean
}) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-xs font-medium text-muted">
      {!compact && <Icon name="languages" className="hidden h-4 w-4 sm:block" />}
      <select
        aria-label="번역 방향"
        value={sourceLang}
        onChange={(event) => onChange(event.target.value as SourceLanguage)}
        className={`min-h-9 max-w-full cursor-pointer rounded-lg border border-subtle bg-subtle py-1.5 pr-5 text-xs text-ink sm:border-transparent sm:bg-transparent ${compact ? 'w-[6rem] pl-1.5' : 'pl-2.5'}`}
      >
        <option value="en">{compact ? 'EN → 한' : 'English → 한국어'}</option>
        <option value="ko">{compact ? '한 → EN' : '한국어 → English'}</option>
        <option value="auto">{compact ? '자동' : '언어 자동 감지'}</option>
      </select>
      {!compact && sourceLang === 'auto' && detectedLang && (
        <span className="ui-badge hidden sm:inline-flex" title="현재 감지된 언어">
          {detectedLang === 'ko' ? '한국어' : 'English'}
        </span>
      )}
    </label>
  )
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
  sourceLang,
  onChangeLangDirection,
  detectedLang,
}: Props) {
  const { isDark, updateTheme } = useTheme()
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
    <header className="relative z-20 flex h-14 shrink-0 items-center border-b border-subtle bg-panel px-3 sm:px-4">
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

      <div className="ml-4 hidden sm:block">
        <LanguageDirection
          sourceLang={sourceLang}
          onChange={onChangeLangDirection}
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
          className="ui-icon-button"
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
          className="ui-icon-button"
        >
          <Icon name="settings" className="h-[18px] w-[18px]" />
        </button>
        {onLogout && (
          <button
            type="button"
            onClick={onLogout}
            aria-label="로그아웃"
            title="로그아웃"
            className="ui-icon-button"
          >
            <Icon name="logout" />
          </button>
        )}
      </div>
    </header>
  )
}
