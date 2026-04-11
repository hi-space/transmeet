'use client'

import { useState, useRef, useCallback } from 'react'
import { api } from '@/lib/api'
import type { Settings } from '@/hooks/useSettings'
import { useQuickTranslateHistory, type TranslationRecord } from '@/hooks/useQuickTranslateHistory'

interface Props {
  settings: Settings
  onClose: () => void
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

export default function QuickTranslatePopup({ settings, onClose }: Props) {
  const [input, setInput] = useState('')
  const [result, setResult] = useState<{ englishText: string; audioData?: string } | null>(null)
  const [isTranslating, setIsTranslating] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const { history, addRecord, deleteRecord, clearAll } = useQuickTranslateHistory()

  const handleTranslate = useCallback(async () => {
    const text = input.trim()
    if (!text || isTranslating) return

    setIsTranslating(true)
    setError(null)
    setResult(null)

    try {
      const res = await api.tts.synthesize(
        text,
        settings.pollyEngine,
        settings.pollyVoiceId,
        true,
        settings.translationModel
      )
      setResult({ englishText: res.translatedText, audioData: res.audioData })
      addRecord({
        koreanText: text,
        englishText: res.translatedText,
        audioData: res.audioData,
      })
    } catch {
      setError('번역에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsTranslating(false)
    }
  }, [input, isTranslating, settings, addRecord])

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
                className="w-4 h-4 text-indigo-500"
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
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          {/* Input */}
          <div className="mb-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="한국어를 입력하세요..."
              rows={3}
              className="w-full px-3 py-2.5 text-sm rounded-xl bg-white/80 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/60 focus:outline-none focus:ring-2 focus:ring-indigo-400/50 resize-none text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500"
            />
          </div>

          {/* Translate button */}
          <button
            onClick={handleTranslate}
            disabled={!input.trim() || isTranslating}
            className="w-full py-2 rounded-xl text-sm font-medium text-white bg-gradient-to-r from-indigo-500 to-violet-600 hover:opacity-90 active:scale-[0.98] transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
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

          {/* Result */}
          {result && (
            <div className="mt-3 p-3 rounded-xl bg-indigo-50/60 dark:bg-indigo-900/20 border border-indigo-200/60 dark:border-indigo-500/20">
              <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                {result.englishText}
              </p>
              <div className="flex items-center gap-2 mt-2">
                {/* TTS Play */}
                <button
                  onClick={() => handlePlay(result.audioData)}
                  disabled={isPlaying || !result.audioData}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-indigo-600 dark:text-indigo-400 bg-indigo-100/60 dark:bg-indigo-500/15 hover:bg-indigo-100 dark:hover:bg-indigo-500/25 transition-all disabled:opacity-40"
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
                  onClick={() => handleCopy(result.englishText)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium text-slate-500 dark:text-slate-400 bg-slate-100/60 dark:bg-white/6 hover:bg-slate-200/60 dark:hover:bg-white/10 transition-all"
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
                    className="p-2.5 rounded-xl bg-white/60 dark:bg-slate-800/40 border border-slate-200/60 dark:border-white/8"
                  >
                    <p className="text-xs text-slate-500 dark:text-slate-400 mb-1 line-clamp-1">
                      {record.koreanText}
                    </p>
                    <p className="text-sm text-slate-700 dark:text-slate-200 line-clamp-2">
                      {record.englishText}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      {record.audioData && (
                        <button
                          onClick={() => handleHistoryPlay(record)}
                          disabled={isPlaying}
                          className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium text-indigo-500 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-500/10 transition-all disabled:opacity-40"
                        >
                          <svg viewBox="0 0 24 24" fill="currentColor" className="w-2.5 h-2.5">
                            <path d="M8 5v14l11-7z" />
                          </svg>
                          재생
                        </button>
                      )}
                      <button
                        onClick={() => handleCopy(record.englishText)}
                        className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-medium text-slate-400 dark:text-slate-500 hover:bg-slate-100 dark:hover:bg-white/6 transition-all"
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
