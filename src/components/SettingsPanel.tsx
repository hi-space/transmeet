'use client'

import type { Settings } from '@/hooks/useSettings'

interface Props {
  settings: Settings
  onUpdate: (patch: Partial<Settings>) => void
  onClose: () => void
}

// ─── Polly voice catalogue ───────────────────────────────────────────────────

const VOICES: Record<'en' | 'ko', { id: string; label: string; engines: string[] }[]> = {
  en: [
    { id: 'Joanna', label: 'Joanna', engines: ['generative', 'neural', 'standard'] },
    { id: 'Ruth', label: 'Ruth', engines: ['generative', 'neural', 'standard'] },
    { id: 'Tiffany', label: 'Tiffany', engines: ['generative', 'neural', 'standard'] },
  ],
  ko: [{ id: 'Seoyeon', label: 'Seoyeon', engines: ['neural'] }],
}

const DEFAULT_VOICE: Record<'en' | 'ko', { id: string; engine: Settings['pollyEngine'] }> = {
  en: { id: 'Ruth', engine: 'generative' },
  ko: { id: 'Seoyeon', engine: 'neural' },
}

// ─── Style helpers ───────────────────────────────────────────────────────────

const pill = (active: boolean) =>
  `px-3 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer ${
    active
      ? 'bg-indigo-500 text-white shadow-sm shadow-indigo-500/30'
      : 'bg-slate-100 dark:bg-white/6 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-white/10'
  }`

// ─── Component ───────────────────────────────────────────────────────────────

export default function SettingsPanel({ settings, onUpdate, onClose }: Props) {
  // Wrap onUpdate so language/engine changes auto-correct dependent fields
  const handleUpdate = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch }

    // Target language changed → reset voice/engine to language default
    if (patch.targetLang && patch.targetLang !== settings.targetLang) {
      const d = DEFAULT_VOICE[patch.targetLang]
      next.pollyVoiceId = d.id
      next.pollyEngine = d.engine
    }

    // Engine changed → ensure current voice supports new engine
    if (patch.pollyEngine && patch.pollyEngine !== settings.pollyEngine) {
      const voices = VOICES[next.targetLang]
      const current = voices.find((v) => v.id === next.pollyVoiceId)
      if (!current || !current.engines.includes(patch.pollyEngine)) {
        const fallback = voices.find((v) => v.engines.includes(patch.pollyEngine!))
        if (fallback) next.pollyVoiceId = fallback.id
      }
    }

    onUpdate(next)
  }

  const availableVoices = VOICES[settings.targetLang].filter((v) =>
    v.engines.includes(settings.pollyEngine)
  )

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />

      {/* Sheet / Modal */}
      <div
        className="relative z-10 w-full sm:max-w-sm glass-panel sm:rounded-2xl rounded-t-2xl shadow-xl shadow-black/20 slide-up-fade flex flex-col"
        style={{ maxHeight: '65dvh' }}
      >
        {/* Scrollable content */}
        <div
          className="overflow-y-auto flex-1 p-5"
          style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-4 h-4 text-indigo-500"
              >
                <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">설정</h2>
            </div>
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/8 transition-all"
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

          {/* ── STT engine ─────────────────────────────────────────────────── */}
          <div className="space-y-2">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              STT 엔진
            </p>
            <div className="flex gap-1.5">
              {(
                [
                  ['whisper', 'SageMaker Whisper'],
                  ['transcribe', 'AWS Transcribe'],
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
              <p className="text-[10px] text-slate-400 dark:text-slate-500">
                발화 단위 인식 • 화자 분리 지원 (영어) • en/ko 자동 감지
              </p>
            )}
          </div>

          {/* Divider */}
          <div className="my-4 h-px bg-slate-200/60 dark:bg-white/8" />

          {/* ── Translation timing ──────────────────────────────────────────── */}
          <div className="space-y-2">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              번역 타이밍
            </p>
            <div className="flex flex-wrap gap-1.5">
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
            <p className="text-[10px] text-slate-400 dark:text-slate-500">
              {settings.translationTiming === 'sentence' &&
                '마침표/물음표 감지 또는 2초 무음 후 번역'}
              {settings.translationTiming === 'realtime' && '모든 발화 세그먼트마다 즉시 번역'}
              {settings.translationTiming === 'manual' && '자동 번역 없음 · 버튼 클릭 시에만 번역'}
            </p>
          </div>

          {/* Divider */}
          <div className="my-4 h-px bg-slate-200/60 dark:bg-white/8" />

          {/* ── Silence timeout ─────────────────────────────────────────────── */}
          <div className="space-y-2">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              말풍선 분리 기준
            </p>
            <div className="flex flex-wrap gap-1.5">
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
            <p className="text-[10px] text-slate-400 dark:text-slate-500">
              같은 화자가 이 시간 이상 침묵하면 새 말풍선 시작
            </p>
          </div>

          {/* Divider */}
          <div className="my-4 h-px bg-slate-200/60 dark:bg-white/8" />

          {/* ── Translation model ───────────────────────────────────────────── */}
          <div className="space-y-2">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              번역 모델
            </p>
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ['us.amazon.nova-micro-v1:0', 'Nova Micro'],
                  ['global.amazon.nova-2-lite-v1:0', 'Nova 2 Lite'],
                  ['global.anthropic.claude-haiku-4-5-20251001-v1:0', 'Haiku 4.5'],
                ] as const
              ).map(([val, label]) => (
                <button
                  key={val}
                  onClick={() => handleUpdate({ translationModel: val })}
                  className={pill(settings.translationModel === val)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Divider */}
          <div className="my-4 h-px bg-slate-200/60 dark:bg-white/8" />

          {/* ── Auto-summarize settings ─────────────────────────────────────── */}
          <div className="space-y-3">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              자동 요약
            </p>
            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">요약 주기 (녹음 중)</p>
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    [0, 'Off'],
                    [1, '1분'],
                    [2, '2분'],
                    [5, '5분'],
                    [10, '10분'],
                  ] as const
                ).map(([val, label]) => (
                  <button
                    key={val}
                    onClick={() => handleUpdate({ autoSummarizeInterval: val })}
                    className={pill(settings.autoSummarizeInterval === val)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Divider */}
          <div className="my-4 h-px bg-slate-200/60 dark:bg-white/8" />

          {/* ── TTS settings ────────────────────────────────────────────────── */}
          <div className="space-y-4">
            <p className="text-[11px] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
              Polly (TTS)
            </p>

            {/* Auto play toggle */}
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-slate-600 dark:text-slate-300">자동 재생</span>
              <button
                onClick={() => handleUpdate({ ttsAutoPlay: !settings.ttsAutoPlay })}
                aria-label="TTS 자동 재생 토글"
                className={`relative shrink-0 w-10 h-5 rounded-full transition-colors duration-200 focus:outline-none ${
                  settings.ttsAutoPlay ? 'bg-indigo-500' : 'bg-slate-200 dark:bg-white/15'
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
              <div className="flex flex-wrap gap-1.5">
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
  )
}
