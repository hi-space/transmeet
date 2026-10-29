'use client'

import { useState, type ReactNode } from 'react'
import { useTheme } from '@/components/ThemeProvider'
import ThemePresetPicker from '@/components/ThemePresetPicker'
import type { Settings } from '@/hooks/useSettings'
import type { ThemeSettings as ThemePreferences } from '@/lib/theme'

const ACCENTS: { value: ThemePreferences['accent']; label: string; color: string }[] = [
  { value: 'cyan', label: '청록', color: '#34756f' },
  { value: 'amazon', label: '오렌지', color: '#ff9900' },
  { value: 'blue', label: '블루', color: '#4b6f9e' },
  { value: 'violet', label: '라벤더', color: '#7c6197' },
  { value: 'emerald', label: '세이지', color: '#55765c' },
  { value: 'rose', label: '로즈', color: '#a25670' },
]

const FONTS: {
  value: ThemePreferences['font']
  label: string
  shortLabel: string
  family: string
}[] = [
  {
    value: 'default',
    label: '테마 기본 글꼴',
    shortLabel: '기본',
    family: 'var(--theme-font)',
  },
  {
    value: 'sans',
    label: '고딕 · Noto Sans',
    shortLabel: 'Noto',
    family: 'var(--font-noto-sans), sans-serif',
  },
  {
    value: 'plex',
    label: 'IBM Plex Sans KR',
    shortLabel: 'Plex',
    family: 'var(--font-ibm-plex), system-ui, sans-serif',
  },
  {
    value: 'mono',
    label: '고정폭 · JetBrains',
    shortLabel: 'Mono',
    family: 'var(--font-geist-mono), var(--font-noto-sans), monospace',
  },
]

function CheckMark({ className = 'h-3.5 w-3.5' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="m5 12 4 4L19 6" />
    </svg>
  )
}

function Choice({
  selected,
  onClick,
  label,
  children,
}: {
  selected: boolean
  onClick: () => void
  label: string
  children?: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={selected}
      onClick={onClick}
      className="theme-choice flex min-h-10 min-w-0 w-full items-center justify-center px-1.5 py-1.5 text-xs font-medium transition-colors motion-reduce:transition-none [@media(pointer:coarse)]:min-h-[44px]"
    >
      {children ?? (
        <span className="flex min-w-0 items-center justify-center gap-1">
          {selected && <CheckMark className="h-3 w-3 shrink-0" />}
          <span>{label}</span>
        </span>
      )}
    </button>
  )
}

function SettingRow({
  id,
  label,
  children,
  stacked = false,
}: {
  id: string
  label: string
  children: ReactNode
  stacked?: boolean
}) {
  return (
    <div className={`grid gap-2 ${stacked ? '' : 'sm:grid-cols-[6rem_minmax(0,1fr)] sm:gap-4'}`}>
      <p
        id={`${id}-label`}
        className={`text-[0.8125rem] font-medium leading-5 text-ink ${stacked ? '' : 'sm:pt-2.5'}`}
      >
        {label}
      </p>
      <div role="group" aria-labelledby={`${id}-label`} className="min-w-0">
        {children}
      </div>
    </div>
  )
}

interface Props {
  voiceFontSize: Settings['voiceFontSize']
  onVoiceFontSizeChange: (size: Settings['voiceFontSize']) => void
}

export default function ThemeSettings({ voiceFontSize, onVoiceFontSizeChange }: Props) {
  const { theme, updateTheme, selectPreset, resetTheme, storageUnavailable } = useTheme()
  const [resetComplete, setResetComplete] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)

  const update = (patch: Partial<ThemePreferences>) => {
    setResetComplete(false)
    updateTheme(patch)
  }

  return (
    <div className="space-y-4">
      {storageUnavailable && (
        <p role="status" className="ui-setting-hint">
          저장할 수 없어 이번 방문에만 적용됩니다.
        </p>
      )}

      <ThemePresetPicker
        value={theme.preset}
        onChange={(preset) => {
          setResetComplete(false)
          selectPreset(preset)
        }}
      />

      <SettingRow id="theme-mode" label="화면 모드">
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              ['system', '시스템'],
              ['light', '라이트'],
              ['dark', '다크'],
            ] as const
          ).map(([value, label]) => (
            <Choice
              key={value}
              label={label}
              selected={theme.mode === value}
              onClick={() => update({ mode: value })}
            />
          ))}
        </div>
      </SettingRow>

      <SettingRow id="theme-accent" label="포인트 색상">
        <div className="grid grid-cols-2 gap-2 min-[360px]:grid-cols-3">
          <div className="col-span-full">
            <Choice
              label="테마 기본 색상"
              selected={theme.accent === 'theme'}
              onClick={() => update({ accent: 'theme' })}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className="h-4 w-4 rounded-full border border-black/10"
                  style={{ backgroundColor: 'rgb(var(--theme-primary))' }}
                  aria-hidden="true"
                />
                테마 기본
                {theme.accent === 'theme' && <CheckMark className="h-3 w-3" />}
              </span>
            </Choice>
          </div>
          {ACCENTS.map(({ value, label, color }) => (
            <Choice
              key={value}
              label={label}
              selected={theme.accent === value}
              onClick={() => update({ accent: value })}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-black/10"
                  style={{ backgroundColor: color }}
                  aria-hidden="true"
                >
                  {theme.accent === value && (
                    <CheckMark className="h-3 w-3 text-primary-foreground" />
                  )}
                </span>
                <span>{label}</span>
              </span>
            </Choice>
          ))}
        </div>
      </SettingRow>

      <SettingRow id="theme-font" label="글꼴" stacked>
        <div className="grid grid-cols-4 gap-2">
          {FONTS.map(({ value, label, shortLabel, family }) => (
            <Choice
              key={value}
              label={label}
              selected={theme.font === value}
              onClick={() => update({ font: value })}
            >
              <span className="flex w-full min-w-0 flex-col items-center gap-1.5 py-1">
                <span
                  style={{ fontFamily: family }}
                  className="whitespace-nowrap text-sm font-normal leading-6 sm:text-lg"
                  aria-hidden="true"
                >
                  <span className="sm:hidden">가Aa</span>
                  <span className="hidden sm:inline">한글 Aa</span>
                </span>
                <span
                  className={`whitespace-nowrap text-[0.6875rem] leading-4 ${
                    theme.font === value ? 'underline underline-offset-4' : ''
                  }`}
                >
                  {shortLabel}
                </span>
              </span>
            </Choice>
          ))}
        </div>
      </SettingRow>

      <SettingRow id="theme-scale" label="화면 크기">
        <div className="grid grid-cols-2 gap-2 min-[360px]:grid-cols-4">
          {([90, 100, 110, 120] as const).map((scale) => (
            <Choice
              key={scale}
              label={`${scale}%`}
              selected={theme.scale === scale}
              onClick={() => update({ scale })}
            />
          ))}
        </div>
      </SettingRow>

      <SettingRow id="theme-voice-size" label="말풍선 크기">
        <div className="grid grid-cols-2 gap-2 min-[360px]:grid-cols-4">
          {(
            [
              ['compact', '작게'],
              ['md', '보통'],
              ['lg', '크게'],
              ['xl', '아주 크게'],
            ] as const
          ).map(([value, label]) => (
            <Choice
              key={value}
              label={label}
              selected={voiceFontSize === value}
              onClick={() => {
                setResetComplete(false)
                onVoiceFontSizeChange(value)
              }}
            />
          ))}
        </div>
      </SettingRow>

      <SettingRow id="theme-spacing" label="줄 간격">
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              ['compact', '촘촘하게'],
              ['normal', '기본'],
              ['relaxed', '여유롭게'],
            ] as const
          ).map(([value, label]) => (
            <Choice
              key={value}
              label={label}
              selected={theme.lineSpacing === value}
              onClick={() => update({ lineSpacing: value })}
            />
          ))}
        </div>
      </SettingRow>

      <div className="border-t border-subtle pt-3">
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            aria-expanded={previewOpen}
            aria-controls="theme-preview"
            onClick={() => setPreviewOpen((open) => !open)}
            className="ui-button ui-button-secondary min-h-10 w-full"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              className={`h-3 w-3 ${previewOpen ? 'rotate-90' : ''}`}
            >
              <path d="m9 5 7 7-7 7" />
            </svg>
            미리보기
          </button>
          <button
            type="button"
            onClick={() => {
              resetTheme()
              onVoiceFontSizeChange('compact')
              setResetComplete(true)
            }}
            className="ui-button ui-button-secondary min-h-10 w-full"
          >
            테마 초기화
          </button>
        </div>
        {resetComplete && (
          <p role="status" className="mt-1 text-[0.6875rem] text-muted">
            기본 테마로 되돌렸습니다.
          </p>
        )}
        <section
          id="theme-preview"
          aria-label="테마 미리보기"
          hidden={!previewOpen}
          data-voice-size={voiceFontSize}
          className="ui-surface mt-2 overflow-hidden"
        >
          <div className="flex items-center justify-between gap-2 border-b border-subtle bg-subtle px-3 py-2">
            <p className="text-xs font-semibold text-accent-700 dark:text-accent-300">
              화자 1 <span className="ml-1 font-normal text-muted">09:41</span>
            </p>
            <span className="rounded bg-primary px-1.5 py-0.5 text-[0.625rem] font-medium text-primary-foreground">
              번역 완료
            </span>
          </div>
          <div className="bg-panel p-3">
            <div
              className={`max-h-40 overflow-y-auto break-words leading-reading ${
                {
                  compact: 'text-sm',
                  md: 'text-xl',
                  lg: 'text-3xl',
                  xl: 'text-5xl',
                }[voiceFontSize]
              }`}
            >
              <p className="voice-original text-muted">Let&apos;s make great things together.</p>
              <p className="voice-translation mt-1 text-ink">함께 멋진 일을 만들어 봅시다.</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
