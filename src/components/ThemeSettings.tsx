'use client'

import { useState, type ReactNode } from 'react'
import { useTheme } from '@/components/ThemeProvider'
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

const FONTS: { value: ThemePreferences['font']; label: string; family: string }[] = [
  {
    value: 'default',
    label: '기본 · Inter',
    family: 'var(--font-geist-sans), system-ui, sans-serif',
  },
  { value: 'sans', label: '고딕 · Noto Sans', family: 'var(--font-noto-sans), sans-serif' },
  {
    value: 'plex',
    label: 'IBM Plex Sans KR',
    family: 'var(--font-ibm-plex), system-ui, sans-serif',
  },
  {
    value: 'mono',
    label: '고정폭 · JetBrains',
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
      aria-pressed={selected}
      onClick={onClick}
      className={`inline-flex min-h-8 min-w-8 items-center justify-center whitespace-nowrap rounded-md border px-1.5 py-1 text-xs font-medium transition-colors motion-reduce:transition-none [@media(pointer:coarse)]:min-h-[44px] [@media(pointer:coarse)]:min-w-[44px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-600 dark:focus-visible:outline-accent-400 ${
        selected
          ? 'border-accent-600 bg-accent-50 text-accent-700 dark:border-accent-400 dark:bg-accent-500/10 dark:text-accent-300'
          : 'border-slate-200 bg-white/70 text-slate-600 hover:border-slate-400 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-300 dark:hover:border-slate-500'
      }`}
    >
      {children ?? (
        <span className="flex items-center justify-center gap-1">
          {selected && <CheckMark className="h-3 w-3 shrink-0" />}
          {label}
        </span>
      )}
    </button>
  )
}

function SettingRow({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 min-[360px]:grid-cols-[4.5rem_minmax(0,1fr)] min-[360px]:gap-2 sm:grid-cols-[5.5rem_minmax(0,1fr)] sm:gap-3">
      <p
        id={`${id}-label`}
        className="text-xs font-medium leading-5 text-slate-600 dark:text-slate-300 min-[360px]:pt-1.5"
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
  const { theme, updateTheme, resetTheme, storageUnavailable } = useTheme()
  const [resetComplete, setResetComplete] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)

  const update = (patch: Partial<ThemePreferences>) => {
    setResetComplete(false)
    updateTheme(patch)
  }

  return (
    <div className="space-y-3">
      <p role="status" className="text-[0.6875rem] leading-4 text-slate-500 dark:text-slate-400">
        {storageUnavailable
          ? '저장할 수 없어 이번 방문에만 적용됩니다.'
          : '변경 즉시 적용 · 자동 저장'}
      </p>

      <SettingRow id="theme-mode" label="화면 모드">
        <div className="flex flex-wrap gap-1">
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
        <div className="flex max-w-[14rem] flex-wrap gap-1">
          {ACCENTS.map(({ value, label, color }) => (
            <Choice
              key={value}
              label={label}
              selected={theme.accent === value}
              onClick={() => update({ accent: value })}
            >
              <span className="flex items-center gap-1">
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

      <SettingRow id="theme-font" label="글꼴">
        <select
          aria-labelledby="theme-font-label"
          value={theme.font}
          onChange={(event) => update({ font: event.target.value as ThemePreferences['font'] })}
          style={{ fontFamily: FONTS.find(({ value }) => value === theme.font)?.family }}
          className="min-h-8 w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 [@media(pointer:coarse)]:min-h-[44px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:focus-visible:outline-accent-400"
        >
          {FONTS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </SettingRow>

      <SettingRow id="theme-scale" label="화면 크기">
        <div className="flex flex-wrap gap-1">
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
        <div className="flex flex-wrap gap-1">
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
        <div className="flex flex-wrap gap-1">
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

      <div className="border-t border-slate-200 pt-2 dark:border-slate-800">
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            aria-expanded={previewOpen}
            aria-controls="theme-preview"
            onClick={() => setPreviewOpen((open) => !open)}
            className="flex min-h-8 items-center gap-1 rounded-md px-1 text-xs font-medium text-slate-600 [@media(pointer:coarse)]:min-h-[44px] hover:text-accent-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 dark:text-slate-300 dark:hover:text-accent-300"
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
            className="min-h-8 rounded-md px-2 py-1 text-xs font-medium text-slate-500 [@media(pointer:coarse)]:min-h-[44px] hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            테마 초기화
          </button>
        </div>
        {resetComplete && (
          <p role="status" className="mt-1 text-[0.6875rem] text-slate-500 dark:text-slate-400">
            기본 테마로 되돌렸습니다.
          </p>
        )}
        <section
          id="theme-preview"
          aria-label="테마 미리보기"
          hidden={!previewOpen}
          className="mt-2 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700"
        >
          <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
            <p className="text-xs font-semibold text-accent-700 dark:text-accent-300">
              Speaker 1 <span className="ml-1 font-normal text-slate-500">09:41</span>
            </p>
            <span className="rounded bg-primary px-1.5 py-0.5 text-[0.625rem] font-medium text-primary-foreground">
              번역 완료
            </span>
          </div>
          <div className="bg-white/80 p-3 dark:bg-slate-950/50">
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
              <p className="text-slate-800 dark:text-slate-100">
                Let&apos;s make great things together.
              </p>
              <p className="mt-1 text-slate-600 dark:text-slate-400">
                함께 멋진 일을 만들어 봅시다.
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
