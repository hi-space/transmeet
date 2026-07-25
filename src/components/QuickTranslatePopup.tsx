'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { api } from '@/lib/api'
import type { Settings } from '@/hooks/useSettings'
import { useQuickTranslateHistory, type TranslationRecord } from '@/hooks/useQuickTranslateHistory'

interface Props {
  settings: Settings
  onClose: () => void
  sendTranslate?: (
    messageId: string,
    originalText: string,
    speaker: string,
    sourceLang?: string,
    targetLang?: string,
    modelId?: string
  ) => void
  wsConnected?: boolean
  stream?: { text: string; phase: 'translating' | 'done' } | null
  onResetStream?: () => void
}

// ─── 번역 방향 (외부 헤더/설정과 독립, 팝업 전용) ──────────────────────────────

type Lang = 'ko' | 'en'

const DIRECTION_KEY = 'transmeet-quick-translate-direction'
const DEFAULT_DIRECTION: Lang = 'ko' // 기본: 한국어 → English

const DIRECTIONS: { source: Lang; from: string; to: string; title: string }[] = [
  { source: 'ko', from: '한', to: 'EN', title: '한국어 → English' },
  { source: 'en', from: 'EN', to: '한', title: 'English → 한국어' },
]

// 출력 언어에 맞는 Polly 목소리 (ko 는 Seoyeon/neural 만 지원)
const TTS_VOICE: Record<Lang, { voiceId: string; engine: string }> = {
  en: { voiceId: 'Ruth', engine: 'generative' },
  ko: { voiceId: 'Seoyeon', engine: 'neural' },
}

const PLACEHOLDER: Record<Lang, string> = {
  ko: '한국어를 입력하세요...',
  en: 'Enter English text...',
}

function loadDirection(): Lang {
  if (typeof window === 'undefined') return DEFAULT_DIRECTION
  const raw = localStorage.getItem(DIRECTION_KEY)
  return raw === 'ko' || raw === 'en' ? raw : DEFAULT_DIRECTION
}

// ─── Audio helper (동일 패턴: page.tsx playBase64Audio) ────────────────────────

function playBase64Audio(
  base64: string,
  onAudio?: (audio: HTMLAudioElement) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const binary = atob(base64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const blob = new Blob([bytes], { type: 'audio/mp3' })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    onAudio?.(audio)
    audio.onended = () => {
      URL.revokeObjectURL(url)
      resolve()
    }
    audio.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Audio playback failed'))
    }
    audio.play().catch(reject)
  })
}

// ─── Component ─────────────────────────────────────────────────────────────────

export default function QuickTranslatePopup({
  settings,
  onClose,
  sendTranslate,
  wsConnected,
  stream,
  onResetStream,
}: Props) {
  const [input, setInput] = useState('')
  const [result, setResult] = useState<{ text: string; audioData?: string } | null>(null)
  const [isTranslating, setIsTranslating] = useState(false)
  const [isLoadingTts, setIsLoadingTts] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const pendingInputRef = useRef<string>('')
  const { history, addRecord, deleteRecord, clearAll } = useQuickTranslateHistory()

  // 번역 방향은 헤더/설정과 독립적으로 팝업 안에서 관리한다 (기본 한→EN)
  const [sourceLang, setSourceLang] = useState<Lang>(DEFAULT_DIRECTION)
  const targetLang: Lang = sourceLang === 'ko' ? 'en' : 'ko'
  const pendingLangsRef = useRef<{ source: Lang; target: Lang }>({ source: 'ko', target: 'en' })

  // SSR-safe: 저장된 방향은 마운트 후 반영
  useEffect(() => {
    setSourceLang(loadDirection())
  }, [])

  const handleChangeDirection = useCallback((next: Lang) => {
    setSourceLang(next)
    try {
      localStorage.setItem(DIRECTION_KEY, next)
    } catch {
      // ignore
    }
  }, [])

  // 스트리밍 번역 done → TTS 요청 + 결과 확정
  useEffect(() => {
    if (!stream || stream.phase !== 'done' || !stream.text) return
    const translated = stream.text
    const original = pendingInputRef.current
    const { source, target } = pendingLangsRef.current
    const voice = TTS_VOICE[target]
    setIsTranslating(false)
    setIsLoadingTts(true)
    // TTS만 요청 (translateFirst=false)
    api.tts
      .synthesize(translated, voice.engine, voice.voiceId, false)
      .then((res) => {
        setResult({ text: translated, audioData: res.audioData })
        addRecord({
          sourceText: original,
          targetText: translated,
          sourceLang: source,
          targetLang: target,
          audioData: res.audioData,
        })
      })
      .catch(() => {
        // TTS 실패해도 번역 결과는 표시
        setResult({ text: translated })
        addRecord({
          sourceText: original,
          targetText: translated,
          sourceLang: source,
          targetLang: target,
        })
      })
      .finally(() => {
        setIsLoadingTts(false)
        onResetStream?.()
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream?.phase])

  const handleTranslate = useCallback(async () => {
    const text = input.trim()
    if (!text || isTranslating) return

    setIsTranslating(true)
    setError(null)
    setResult(null)
    onResetStream?.()
    pendingInputRef.current = text
    pendingLangsRef.current = { source: sourceLang, target: targetLang }

    // WS 연결 시 스트리밍 번역
    if (wsConnected && sendTranslate) {
      sendTranslate('__quick__', text, 'me', sourceLang, targetLang, settings.translationModel)
      return
    }

    // fallback: REST 동기 번역
    const voice = TTS_VOICE[targetLang]
    try {
      const res = await api.tts.synthesize(
        text,
        voice.engine,
        voice.voiceId,
        true,
        settings.translationModel,
        { sourceLang, targetLang }
      )
      setResult({ text: res.translatedText, audioData: res.audioData })
      addRecord({
        sourceText: text,
        targetText: res.translatedText,
        sourceLang,
        targetLang,
        audioData: res.audioData,
      })
    } catch {
      setError('번역에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsTranslating(false)
    }
  }, [
    input,
    isTranslating,
    sourceLang,
    targetLang,
    settings.translationModel,
    addRecord,
    wsConnected,
    sendTranslate,
    onResetStream,
  ])

  const handlePlay = useCallback(
    async (audioData?: string) => {
      if (!audioData || isPlaying) return
      // 기존 재생 중지
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current = null
      }
      setIsPlaying(true)
      try {
        await playBase64Audio(audioData, (audio) => {
          audioRef.current = audio
        })
      } catch {
        // silent
      } finally {
        audioRef.current = null
        setIsPlaying(false)
      }
    },
    [isPlaying]
  )

  const handleCopy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // fallback
    }
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleTranslate()
      }
    },
    [handleTranslate]
  )

  const handleHistoryPlay = useCallback(
    (record: TranslationRecord) => {
      handlePlay(record.audioData)
    },
    [handlePlay]
  )

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />

      {/* Modal */}
      <div
        className="relative z-10 w-full sm:max-w-md glass-panel sm:rounded-2xl rounded-t-2xl shadow-xl shadow-black/20 slide-up-fade flex flex-col"
        style={{ maxHeight: '80dvh' }}
      >
        <div
          className="overflow-y-auto flex-1 p-5"
          style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="w-4 h-4 text-slate-500"
              >
                <path d="m5 8 6 6" />
                <path d="m4 14 6-6 2-3" />
                <path d="M2 5h12" />
                <path d="M7 2h1" />
                <path d="m22 22-5-10-5 10" />
                <path d="M14 18h6" />
              </svg>
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                빠른 번역
              </h2>
            </div>
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-4 h-4"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* 번역 방향 — 헤더 설정과 별개로 이 팝업에서만 적용된다 */}
          <div className="mb-3 flex items-center p-0.5 rounded-lg bg-slate-100 dark:bg-slate-800/80 w-fit">
            {DIRECTIONS.map(({ source, from, to, title }) => {
              const active = sourceLang === source
              return (
                <button
                  key={source}
                  onClick={() => handleChangeDirection(source)}
                  title={title}
                  aria-label={title}
                  aria-pressed={active}
                  className={`flex items-center gap-0.5 px-2 py-1 rounded-md text-[11px] font-bold tracking-tight transition-all ${
                    active
                      ? 'bg-white dark:bg-slate-700 text-cyan-700 dark:text-cyan-300 shadow-sm'
                      : 'text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                >
                  <span>{from}</span>
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="w-2.5 h-2.5 opacity-60"
                  >
                    <path d="M5 12h14M13 6l6 6-6 6" />
                  </svg>
                  <span>{to}</span>
                </button>
              )
            })}
          </div>

          {/* Input */}
          <div className="mb-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={PLACEHOLDER[sourceLang]}
              rows={3}
              className="w-full px-3 py-2.5 text-sm rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-500 resize-none text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500"
            />
          </div>

          {/* Translate button */}
          <button
            onClick={handleTranslate}
            disabled={!input.trim() || isTranslating || isLoadingTts}
            className="w-full py-2 rounded-xl text-sm font-medium text-white bg-cyan-600 hover:bg-cyan-700 active:scale-[0.98] transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {isTranslating ? (
              <>
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                번역 중...
              </>
            ) : (
              '번역하기'
            )}
          </button>

          {/* Error */}
          {error && (
            <div className="mt-3 px-3 py-2 rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200/60 dark:border-red-500/20 text-xs text-red-600 dark:text-red-400">
              {error}
            </div>
          )}

          {/* Streaming result */}
          {isTranslating && stream && stream.text && (
            <div className="mt-3 p-3 rounded-xl bg-cyan-50 dark:bg-cyan-950 border border-cyan-200 dark:border-cyan-800">
              <p className="text-sm text-cyan-700 dark:text-cyan-300 leading-relaxed">
                {stream.text}
                <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
              </p>
            </div>
          )}

          {/* TTS 로딩 */}
          {isLoadingTts && stream?.phase === 'done' && (
            <div className="mt-3 p-3 rounded-xl bg-cyan-50 dark:bg-cyan-950 border border-cyan-200 dark:border-cyan-800">
              <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                {stream.text}
              </p>
              <div className="flex items-center gap-1 mt-2 text-[11px] text-cyan-600 dark:text-cyan-400">
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                음성 생성 중...
              </div>
            </div>
          )}

          {/* Final result */}
          {result && !isTranslating && !isLoadingTts && (
            <div className="mt-3 p-3 rounded-xl bg-cyan-50 dark:bg-cyan-950 border border-cyan-200 dark:border-cyan-800">
              <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                {result.text}
              </p>
              <div className="flex items-center gap-2 mt-2">
                {/* TTS Play */}
                <button
                  onClick={() => handlePlay(result.audioData)}
                  disabled={isPlaying || !result.audioData}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-cyan-700 dark:text-cyan-300 bg-cyan-100 dark:bg-cyan-500/20 hover:bg-cyan-200 dark:hover:bg-cyan-500/30 transition-all disabled:opacity-40"
                >
                  {isPlaying ? (
                    <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="currentColor" className="w-3 h-3">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  )}
                  재생
                </button>

                {/* Copy */}
                <button
                  onClick={() => handleCopy(result.text)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 transition-all"
                >
                  {copied ? (
                    <>
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        className="w-3 h-3 text-green-500"
                      >
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      복사됨
                    </>
                  ) : (
                    <>
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        className="w-3 h-3"
                      >
                        <rect width="14" height="14" x="8" y="8" rx="2" />
                        <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
                      </svg>
                      복사
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* History */}
          {history.length > 0 && (
            <div className="mt-5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 tracking-wide uppercase">
                  최근 번역
                </span>
                <button
                  onClick={clearAll}
                  className="text-[10px] text-slate-400 dark:text-slate-500 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                >
                  전체 삭제
                </button>
              </div>
              <div className="space-y-2">
                {history.map((record) => (
                  <div
                    key={record.id}
                    className="p-2.5 rounded-xl bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700"
                  >
                    <p className="text-xs text-slate-500 dark:text-slate-400 mb-1 line-clamp-1">
                      {record.sourceText}
                    </p>
                    <p className="text-sm text-slate-700 dark:text-slate-200 line-clamp-2">
                      {record.targetText}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      {record.audioData && (
                        <button
                          onClick={() => handleHistoryPlay(record)}
                          disabled={isPlaying}
                          className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium text-cyan-600 dark:text-cyan-400 hover:bg-cyan-100 dark:hover:bg-cyan-500/10 transition-all disabled:opacity-40"
                        >
                          <svg viewBox="0 0 24 24" fill="currentColor" className="w-2.5 h-2.5">
                            <path d="M8 5v14l11-7z" />
                          </svg>
                          재생
                        </button>
                      )}
                      <button
                        onClick={() => handleCopy(record.targetText)}
                        className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium text-slate-400 dark:text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 transition-all"
                      >
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="w-2.5 h-2.5"
                        >
                          <rect width="14" height="14" x="8" y="8" rx="2" />
                          <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
                        </svg>
                        복사
                      </button>
                      <button
                        onClick={() => deleteRecord(record.id)}
                        className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium text-slate-400 dark:text-slate-500 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                      >
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="w-2.5 h-2.5"
                        >
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
