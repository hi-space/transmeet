'use client'

import { THEME_PRESETS, type ThemeSettings } from '@/lib/theme'

interface Props {
  value: ThemeSettings['preset']
  onChange: (preset: ThemeSettings['preset']) => void
}

export default function ThemePresetPicker({ value, onChange }: Props) {
  return (
    <section aria-labelledby="theme-preset-label" className="space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="theme-preset-label" className="ui-setting-label">
          테마 스타일
        </h3>
        <button
          type="button"
          aria-pressed={value === 'default'}
          onClick={() => onChange('default')}
          className={`ui-button min-h-9 px-2 ${value === 'default' ? 'ui-button-tinted' : ''}`}
        >
          기본 스타일
        </button>
      </div>
      <div role="group" aria-labelledby="theme-preset-label" className="grid grid-cols-2 gap-2.5">
        {THEME_PRESETS.map((preset) => (
          <button
            key={preset.value}
            type="button"
            aria-label={`${preset.label} 테마`}
            aria-pressed={value === preset.value}
            onClick={() => onChange(preset.value)}
            className="theme-preset-option last:odd:col-span-2"
          >
            <span
              aria-hidden="true"
              className="theme-preset-sample"
              data-preview-preset={preset.value}
            >
              <span className="theme-sample-sidebar" />
              <span className="theme-sample-content">
                <span className="theme-sample-heading" />
                <span className="theme-sample-card" />
                <span className="theme-sample-card" />
              </span>
            </span>
            <span className="mt-2 flex min-w-0 items-start justify-between gap-1">
              <span className="min-w-0 text-xs font-semibold leading-5">{preset.label}</span>
              {value === preset.value && (
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                  className="mt-0.5 h-4 w-4 shrink-0 text-accent-700 dark:text-accent-200"
                >
                  <path d="m5 12 4 4L19 6" />
                </svg>
              )}
            </span>
            <span className="mt-0.5 block break-keep text-[0.6875rem] leading-4 text-muted">
              {preset.description}
            </span>
          </button>
        ))}
      </div>
      <p className="ui-setting-hint">
        배경·글꼴·카드 모양을 함께 바꿉니다. 선택 후에도 아래 설정을 조절할 수 있습니다.
      </p>
    </section>
  )
}
