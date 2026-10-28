'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { api } from '@/lib/api'
import type { Settings } from '@/hooks/useSettings'
import { useQuickTranslateHistory, type TranslationRecord } from '@/hooks/useQuickTranslateHistory'
import Dialog from '@/components/ui/Dialog'
import Icon from '@/components/ui/Icon'

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
  { source: 'ko', from: '한국어', to: 'English', title: '한국어 → English' },
  { source: 'en', from: 'English', to: '한국어', title: 'English → 한국어' },
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
  try {
    const raw = localStorage.getItem(DIRECTION_KEY)
    return raw === 'ko' || raw === 'en' ? raw : DEFAULT_DIRECTION
  } catch {
    return DEFAULT_DIRECTION
  }
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
    const finish = () => {
      URL.revokeObjectURL(url)
      resolve()
    }
    audio.onended = finish
    audio.onpause = finish
    audio.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Audio playback failed'))
    }
    audio.play().catch((error) => {
      URL.revokeObjectURL(url)
      reject(error)
    })
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
  const [copied, setCopied] = useState<string | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
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
    audioRef.current?.pause()
    audioRef.current = null
    setIsPlaying(false)
    setSourceLang(next)
    setResult(null)
    setError(null)
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
    if (!text || isTranslating || isLoadingTts) return

    audioRef.current?.pause()
    audioRef.current = null
    setIsPlaying(false)
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
    isLoadingTts,
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
      setCopied(text)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      setError('복사하지 못했습니다. 다시 시도해 주세요.')
    }
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.nativeEvent.isComposing || e.keyCode === 229) return
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

  useEffect(
    () => () => {
      audioRef.current?.pause()
    },
    []
  )

  const stopAudio = () => {
    audioRef.current?.pause()
    audioRef.current = null
    setIsPlaying(false)
  }
  const busy = isTranslating || isLoadingTts
  const resultText = isTranslating || isLoadingTts ? stream?.text : result?.text

  return (
    <Dialog labelledBy="quick-translate-title" onClose={onClose} initialFocusRef={inputRef}>
      <div className="flex shrink-0 items-center justify-between border-b border-subtle px-5 py-3">
        <div className="flex items-center gap-2.5">
          <Icon
            name="languages"
            className="h-[18px] w-[18px] text-accent-600 dark:text-accent-400"
          />
          <h2 id="quick-translate-title" className="text-base font-semibold">
            빠른 번역
          </h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="빠른 번역 닫기"
          className="ui-icon-button"
        >
          <Icon name="close" />
        </button>
      </div>
      <div
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain p-5"
        style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
      >
        <div
          className="mb-4 grid grid-cols-2 gap-1 rounded-lg bg-subtle p-1"
          role="group"
          aria-label="빠른 번역 방향"
        >
          {DIRECTIONS.map(({ source, from, to, title }) => (
            <button
              key={source}
              type="button"
              onClick={() => handleChangeDirection(source)}
              disabled={busy}
              aria-label={title}
              aria-pressed={sourceLang === source}
              className={`ui-button min-w-0 gap-1.5 px-2 ${sourceLang === source ? 'ui-button-secondary text-accent-700 shadow-sm dark:text-accent-300' : ''}`}
            >
              <span>{from}</span>
              <Icon name="arrowRight" className="h-3 w-3" />
              <span>{to}</span>
            </button>
          ))}
        </div>
        <label htmlFor="quick-translate-input" className="ui-setting-label mb-2 block">
          번역할 내용
        </label>
        <textarea
          ref={inputRef}
          id="quick-translate-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={PLACEHOLDER[sourceLang]}
          rows={4}
          className="ui-input resize-y leading-relaxed"
          style={{ minHeight: '7rem', maxHeight: '16rem' }}
        />
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            onClick={handleTranslate}
            disabled={!input.trim() || busy}
            className="ui-button ui-button-primary min-w-28"
          >
            <Icon
              name={busy ? 'loader' : 'languages'}
              className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`}
            />
            {isTranslating ? '번역 중…' : isLoadingTts ? '음성 준비 중…' : '번역하기'}
          </button>
        </div>
        {error && (
          <p
            role="alert"
            className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600 dark:border-red-900 dark:bg-red-500/10 dark:text-red-400"
          >
            {error}
          </p>
        )}
        {resultText && (
          <div className="mt-4 rounded-xl border border-accent-200 bg-accent-50/60 p-4 dark:border-accent-800 dark:bg-accent-500/5">
            <p className="mb-2 text-[0.6875rem] font-medium text-accent-700 dark:text-accent-300">
              {targetLang === 'en' ? 'English' : '한국어'}
            </p>
            <p className="whitespace-pre-wrap break-words text-sm leading-reading text-ink">
              {resultText}
              {isTranslating && (
                <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse bg-accent-500 align-middle" />
              )}
            </p>
            {!busy && result && (
              <div className="mt-3 flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => (isPlaying ? stopAudio() : handlePlay(result.audioData))}
                  disabled={!result.audioData}
                  className="ui-button ui-button-tinted gap-1.5"
                >
                  <Icon name={isPlaying ? 'stop' : 'volume'} className="h-3.5 w-3.5" />
                  {isPlaying ? '재생 중지' : '음성 재생'}
                </button>
                <button
                  type="button"
                  onClick={() => handleCopy(result.text)}
                  className="ui-button gap-1.5"
                >
                  <Icon name={copied === result.text ? 'check' : 'copy'} className="h-3.5 w-3.5" />
                  {copied === result.text ? '복사됨' : '복사'}
                </button>
              </div>
            )}
          </div>
        )}
        {history.length > 0 && (
          <div className="mt-5 border-t border-subtle pt-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-muted">
                최근 번역 <span className="ml-1 font-normal tabular-nums">{history.length}</span>
              </h3>
              {confirmClear ? (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setConfirmClear(false)}
                    className="ui-button"
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      clearAll()
                      setConfirmClear(false)
                    }}
                    className="ui-button text-red-600 dark:text-red-400"
                  >
                    모두 삭제
                  </button>
                </div>
              ) : (
                <button type="button" onClick={() => setConfirmClear(true)} className="ui-button">
                  전체 삭제
                </button>
              )}
            </div>
            <div className="space-y-2">
              {history.map((record) => (
                <article key={record.id} className="rounded-lg border border-subtle bg-subtle p-3">
                  <p className="mb-1 line-clamp-2 break-words text-xs text-muted">
                    {record.sourceText}
                  </p>
                  <p className="break-words text-sm leading-relaxed text-ink">
                    {record.targetText}
                  </p>
                  <div className="mt-2 flex items-center gap-1">
                    {record.audioData && (
                      <button
                        type="button"
                        onClick={() => handleHistoryPlay(record)}
                        disabled={isPlaying}
                        className="ui-button gap-1.5"
                      >
                        <Icon name="volume" className="h-3.5 w-3.5" />
                        재생
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleCopy(record.targetText)}
                      className="ui-button gap-1.5"
                    >
                      <Icon
                        name={copied === record.targetText ? 'check' : 'copy'}
                        className="h-3.5 w-3.5"
                      />
                      {copied === record.targetText ? '복사됨' : '복사'}
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteRecord(record.id)}
                      aria-label="번역 기록 삭제"
                      title="번역 기록 삭제"
                      className="ui-icon-button ml-auto"
                    >
                      <Icon name="trash" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}
      </div>
    </Dialog>
  )
}
