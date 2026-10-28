'use client'

import { useEffect, useRef, useState } from 'react'
import type { Settings } from '@/hooks/useSettings'
import { supportsSystemAudio } from '@/lib/platform'
import ThemeSettings from '@/components/ThemeSettings'
import EmailNotifySetting from '@/components/EmailNotifySetting'
import { TRANSLATION_MODELS } from '@/lib/translationModels'

interface Props {
  settings: Settings
  onUpdate: (patch: Partial<Settings>) => void
  onClose: () => void
}

// ─── Polly voice catalogue ───────────────────────────────────────────────────

const VOICES: Record<'en' | 'ko', { id: string; label: string; engines: string[] }[]> = {
  en: [
    { id: 'Joanna', label: 'Joanna', engines: ['generative', 'neural', 'standard'] },
    { id: 'Ruth', label: 'Ruth', engines: ['generative', 'neural'] },
    { id: 'Tiffany', label: 'Tiffany', engines: ['generative'] },
  ],
  ko: [{ id: 'Seoyeon', label: 'Seoyeon', engines: ['neural'] }],
}

// ─── Style helpers ───────────────────────────────────────────────────────────

const pill = (active: boolean) =>
  `w-full min-w-0 min-h-10 px-2 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer [@media(pointer:coarse)]:min-h-[44px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-600 dark:focus-visible:outline-accent-400 ${
    active
      ? 'bg-primary text-primary-foreground'
      : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700'
  }`

// ─── Component ───────────────────────────────────────────────────────────────

export default function SettingsPanel({ settings, onUpdate, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<'theme' | 'general'>('theme')
  const dialogRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const previousFocus = document.activeElement
    closeRef.current?.focus({ preventScroll: true })

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
      }
      if (event.key !== 'Tab') return
      const dialog = dialogRef.current
      if (!dialog) return
      const elements = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex="0"]'
        )
      ).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0)
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (
        event.shiftKey &&
        (document.activeElement === first || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        last?.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        first?.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement) previousFocus.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [activeTab])

  // 시스템 오디오 지원 여부 — navigator 접근이므로 마운트 후에 판별 (정적 export = SSR)
  const [canUseSystemAudio, setCanUseSystemAudio] = useState(true)
  useEffect(() => {
    setCanUseSystemAudio(supportsSystemAudio())
  }, [])

  // 미지원 기기에서 저장된 system/both 설정이 남아있으면 마이크로 되돌린다
  useEffect(() => {
    if (!canUseSystemAudio && settings.audioSource !== 'mic') {
      onUpdate({ audioSource: 'mic' })
    }
  }, [canUseSystemAudio, settings.audioSource, onUpdate])

  // Wrap onUpdate so engine changes auto-correct dependent fields.
  // sourceLang 변경에 따른 목소리 보정은 useSettings 에서 처리한다 (헤더 토글도 같은 경로).
  const handleUpdate = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch }

    // Engine changed → ensure current voice supports new engine
    const ttsLang = next.sourceLang === 'auto' ? 'en' : next.sourceLang
    if (patch.pollyEngine && patch.pollyEngine !== settings.pollyEngine) {
      const voices = VOICES[ttsLang]
      const current = voices.find((v) => v.id === next.pollyVoiceId)
      if (!current || !current.engines.includes(patch.pollyEngine)) {
        const fallback = voices.find((v) => v.engines.includes(patch.pollyEngine!))
        if (fallback) next.pollyVoiceId = fallback.id
      }
    }

    onUpdate(next)
  }

  const ttsLang = settings.sourceLang === 'auto' ? 'en' : settings.sourceLang
  const availableVoices = VOICES[ttsLang].filter((v) => v.engines.includes(settings.pollyEngine))

  return (
    <div className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center sm:p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />

      {/* Sheet / Modal */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className="relative z-10 w-full min-w-0 sm:max-w-lg glass-panel sm:rounded-xl rounded-t-xl shadow-xl shadow-black/20 slide-up-fade flex flex-col overflow-hidden"
        style={{ maxHeight: 'min(88dvh, 52rem)' }}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between px-5 pt-3 pb-2">
          <div className="flex items-center gap-2">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="w-4 h-4 text-slate-500"
            >
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <h2
              id="settings-title"
              className="text-base font-semibold text-slate-700 dark:text-slate-200"
            >
              설정
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="설정 닫기"
            className="min-w-10 min-h-10 [@media(pointer:coarse)]:min-w-[44px] [@media(pointer:coarse)]:min-h-[44px] flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 dark:focus-visible:outline-accent-400"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="w-4 h-4"
            >
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div
          role="tablist"
          aria-label="설정 분류"
          className="mx-5 mb-1 grid shrink-0 grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800/70"
        >
          {(
            [
              ['theme', '테마'],
              ['general', '음성 · 번역'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              id={`settings-tab-${value}`}
              aria-controls={`settings-panel-${value}`}
              aria-selected={activeTab === value}
              tabIndex={activeTab === value ? 0 : -1}
              onClick={() => setActiveTab(value)}
              onKeyDown={(event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                event.preventDefault()
                const next =
                  event.key === 'Home'
                    ? 'theme'
                    : event.key === 'End'
                      ? 'general'
                      : value === 'theme'
                        ? 'general'
                        : 'theme'
                setActiveTab(next)
                document.getElementById(`settings-tab-${next}`)?.focus()
              }}
              className={`min-h-10 [@media(pointer:coarse)]:min-h-[44px] rounded-md px-3 py-2 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 dark:focus-visible:outline-accent-400 ${
                activeTab === value
                  ? 'bg-white text-accent-700 shadow-sm dark:bg-slate-700 dark:text-accent-300'
                  : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* The title and tabs stay available while the settings scroll. */}
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5 pt-4"
          style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
        >
          <div
            id="settings-panel-theme"
            role="tabpanel"
            aria-labelledby="settings-tab-theme"
            hidden={activeTab !== 'theme'}
          >
            <ThemeSettings
              voiceFontSize={settings.voiceFontSize}
              onVoiceFontSizeChange={(voiceFontSize) => onUpdate({ voiceFontSize })}
            />
          </div>
          <div
            id="settings-panel-general"
            role="tabpanel"
            aria-labelledby="settings-tab-general"
            hidden={activeTab !== 'general'}
          >
            {/* ── Audio source ────────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                오디오 소스
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    ['mic', '마이크'],
                    ['system', '시스템'],
                    ['both', '믹스'],
                  ] as const
                ).map(([val, label]) => {
                  const disabled = val !== 'mic' && !canUseSystemAudio
                  return (
                    <button
                      key={val}
                      onClick={() => !disabled && handleUpdate({ audioSource: val })}
                      disabled={disabled}
                      title={
                        disabled
                          ? 'iOS/모바일 Safari는 시스템 오디오 캡처를 지원하지 않습니다'
                          : undefined
                      }
                      className={`${pill(settings.audioSource === val)} ${
                        disabled ? 'opacity-40 cursor-not-allowed' : ''
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                {!canUseSystemAudio
                  ? '이 기기(iOS/모바일 Safari)는 시스템 오디오 캡처를 지원하지 않아 마이크만 사용합니다'
                  : settings.audioSource === 'mic'
                    ? '마이크 입력만 캡처'
                    : settings.audioSource === 'system'
                      ? '시스템 오디오 캡처 · 화면 공유 시 "오디오 공유" 체크 필요'
                      : '마이크 + 시스템 오디오 믹싱'}
              </p>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── STT engine ─────────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                STT 엔진
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    ['transcribe', 'AWS Transcribe'],
                    ['whisper', 'SageMaker Whisper'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ sttProvider: val })}
                    className={pill(settings.sttProvider === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {settings.sttProvider === 'transcribe' && (
                <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                  발화 단위 인식 • 화자 분리 지원 (영어) • en/ko 자동 감지
                </p>
              )}
            </div>

            {/* 언어 방향(English ↔ 한국어)은 자주 바꾸므로 헤더 토글로 옮겼다 */}

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Translation timing ──────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                번역 타이밍
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    ['sentence', '문장 완료 시'],
                    ['realtime', '실시간'],
                    ['manual', '수동만'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ translationTiming: val })}
                    className={pill(settings.translationTiming === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                {settings.translationTiming === 'sentence' &&
                  '마침표/물음표 감지 또는 2초 무음 후 번역'}
                {settings.translationTiming === 'realtime' && '모든 발화 세그먼트마다 즉시 번역'}
                {settings.translationTiming === 'manual' &&
                  '자동 번역 없음 · 버튼 클릭 시에만 번역'}
              </p>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Auto summarize ───────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                자동 요약
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    [0, '비활성화'],
                    [5, '5개마다'],
                    [10, '10개마다'],
                    [20, '20개마다'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ autoSummarizeMessageCount: val })}
                    className={pill(settings.autoSummarizeMessageCount === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                {settings.autoSummarizeMessageCount === 0
                  ? '자동 요약 비활성화 · 녹음 종료 시에도 요약 안 함'
                  : `메시지 ${settings.autoSummarizeMessageCount}개마다 자동 요약 · 녹음 종료 시에도 요약`}
              </p>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Partial translation mode ─────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                Partial 번역 모드
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    ['sentence', '문장 경계'],
                    ['realtime', '실시간'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ partialTranslationMode: val })}
                    className={pill(settings.partialTranslationMode === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                {settings.partialTranslationMode === 'sentence' &&
                  '마침표/물음표 감지 시 pending 번역 업데이트'}
                {settings.partialTranslationMode === 'realtime' &&
                  '일정 간격마다 최신 partial 번역 (API 호출 증가)'}
              </p>
              {settings.partialTranslationMode === 'realtime' && (
                <div className="mt-2">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">번역 간격</p>
                  <div className="grid grid-flow-col auto-cols-fr gap-2">
                    {(
                      [
                        [500, '0.5초'],
                        [1000, '1초'],
                        [1500, '1.5초'],
                        [2000, '2초'],
                        [3000, '3초'],
                      ] as const
                    ).map(([val, label]) => (
                      <button
                        key={val}
                        onClick={() => handleUpdate({ partialThrottleMs: val })}
                        className={pill(settings.partialThrottleMs === val)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Silence timeout ─────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                말풍선 분리 기준
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    [3000, '3초'],
                    [5000, '5초'],
                    [10000, '10초'],
                    [20000, '20초'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ silenceTimeout: val })}
                    className={pill(settings.silenceTimeout === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                같은 화자가 이 시간 이상 침묵하면 새 말풍선 시작
              </p>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Max sentences per bubble ─────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                말풍선당 최대 문장 수
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {(
                  [
                    [1, '문장마다'],
                    [2, '2문장'],
                    [3, '3문장'],
                    [5, '5문장'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ maxSentencesPerBubble: val })}
                    className={pill(settings.maxSentencesPerBubble === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
                이 문장 수에 도달하면 새 말풍선 시작. 단, 문장이 중간에 끊긴 경우는 항상
                이어붙입니다
              </p>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Meeting end email ───────────────────────────────────────────── */}
            <EmailNotifySetting settings={settings} onUpdate={handleUpdate} />

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── Translation model ───────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                번역 모델
              </p>
              <div className="grid grid-flow-col auto-cols-fr gap-2">
                {TRANSLATION_MODELS.map(({ id, label }) => (
                  <button
                    key={id}
                    onClick={() => handleUpdate({ translationModel: id })}
                    aria-pressed={settings.translationModel === id}
                    className={pill(settings.translationModel === id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Divider */}
            <div className="my-4 h-px bg-slate-200 dark:bg-slate-800" />

            {/* ── TTS settings ────────────────────────────────────────────────── */}
            <div className="space-y-4">
              <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                Polly (TTS)
              </p>

              {/* Auto play toggle */}
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-slate-600 dark:text-slate-300">자동 재생</span>
                <button
                  onClick={() => handleUpdate({ ttsAutoPlay: !settings.ttsAutoPlay })}
                  aria-label="TTS 자동 재생 토글"
                  className={`relative shrink-0 w-10 h-5 rounded-full transition-colors duration-200 focus:outline-none ${
                    settings.ttsAutoPlay ? 'bg-primary' : 'bg-slate-200 dark:bg-slate-700'
                  }`}
                >
                  <span
                    className="absolute top-[2px] w-4 h-4 bg-white rounded-full shadow-sm transition-all duration-200"
                    style={{ left: settings.ttsAutoPlay ? '22px' : '2px' }}
                  />
                </button>
              </div>

              {/* Voice selection */}
              <div>
                <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">목소리</p>
                <div className="grid grid-flow-col auto-cols-fr gap-2">
                  {availableVoices.map((v) => (
                    <button
                      key={v.id}
                      onClick={() => handleUpdate({ pollyVoiceId: v.id })}
                      className={pill(settings.pollyVoiceId === v.id)}
                    >
                      {v.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
