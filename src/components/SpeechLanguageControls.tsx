'use client'

import { useId } from 'react'
import {
  effectiveSourceLanguage,
  type Language,
  type SourceLanguage,
  type TranslationTarget,
} from '@/lib/speechLanguages'
import type { SttProvider } from '@/lib/sttProviders'

interface Props {
  provider: SttProvider
  streaming?: boolean
  sourceLang: SourceLanguage
  translationTarget: TranslationTarget
  detectedLang?: Language | null
  onChangeSourceLang: (source: SourceLanguage) => void
  onChangeTranslationTarget: (target: TranslationTarget) => void
  compact?: boolean
}

export default function SpeechLanguageControls({
  provider,
  streaming = false,
  sourceLang,
  translationTarget,
  detectedLang,
  onChangeSourceLang,
  onChangeTranslationTarget,
  compact = false,
}: Props) {
  const hintId = useId()
  const automaticOnly = provider === 'qwen3-asr' && !streaming
  const selectClass =
    'min-w-0 rounded-lg border border-subtle bg-subtle px-2 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-60 ' +
    (compact ? 'h-11 w-full' : 'h-9')
  const detectedLabel = detectedLang === 'ko' ? '한국어' : 'English'

  return (
    <div className="flex min-w-0 items-center gap-2">
      <label className={`flex min-w-0 items-center ${compact ? 'flex-1' : ''}`}>
        <span className="sr-only">음성 언어</span>
        <select
          aria-label="음성 언어"
          aria-describedby={automaticOnly ? hintId : undefined}
          title={automaticOnly ? 'Qwen3-ASR는 입력 언어를 자동으로 감지합니다' : '음성 입력 언어'}
          value={effectiveSourceLanguage(provider, sourceLang, streaming)}
          disabled={automaticOnly}
          onChange={(event) => onChangeSourceLang(event.target.value as SourceLanguage)}
          className={`${selectClass} ${compact ? '' : 'w-[8.5rem]'}`}
        >
          <option value="auto">입력 · 자동</option>
          <option value="ko">입력 · 한국어</option>
          <option value="en">입력 · English</option>
        </select>
      </label>
      <label className={`flex min-w-0 items-center ${compact ? 'flex-1' : ''}`}>
        <span className="sr-only">번역 대상</span>
        <select
          aria-label="번역 대상"
          title={translationTarget === 'auto' ? '감지된 언어의 반대편으로 번역' : '번역 대상 언어'}
          value={translationTarget}
          onChange={(event) => onChangeTranslationTarget(event.target.value as TranslationTarget)}
          className={`${selectClass} ${compact ? '' : 'w-[9rem]'}`}
        >
          <option value="auto">번역 · 반대편</option>
          <option value="ko">번역 · 한국어</option>
          <option value="en">번역 · English</option>
        </select>
      </label>
      <span
        aria-label="현재 감지 언어"
        aria-live="polite"
        title={
          detectedLang ? `현재 감지 언어: ${detectedLabel}` : '아직 감지된 음성 언어가 없습니다'
        }
        className="inline-flex shrink-0 items-center justify-center gap-1.5 self-stretch whitespace-nowrap text-xs leading-none text-muted"
      >
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${detectedLang ? 'bg-accent-500' : 'bg-slate-300 dark:bg-slate-600'}`}
        />
        {detectedLang ? (compact ? detectedLabel : `감지: ${detectedLabel}`) : '감지 전'}
      </span>
      {automaticOnly && (
        <span id={hintId} className="sr-only">
          Qwen3-ASR는 자동으로 인식합니다. 번역 대상은 선택할 수 있습니다.
        </span>
      )}
    </div>
  )
}
