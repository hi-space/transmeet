'use client'

import { useEffect, useRef } from 'react'
import { Message, SpeakerRole } from '@/types/meeting'

const SPEAKER_CONFIG: Record<
  'speaker1' | 'speaker2',
  {
    label: string
    nameColor: string
    cardBg: string
    translationColor: string
  }
> = {
  speaker1: {
    label: 'Speaker 1',
    nameColor: 'text-blue-600 dark:text-blue-400',
    cardBg: 'bg-blue-50 dark:bg-blue-950 border border-slate-200 dark:border-slate-800',
    translationColor: 'text-slate-500 dark:text-slate-400',
  },
  speaker2: {
    label: 'Speaker 2',
    nameColor: 'text-emerald-600 dark:text-emerald-400',
    cardBg: 'bg-emerald-50 dark:bg-emerald-950 border border-slate-200 dark:border-slate-800',
    translationColor: 'text-slate-500 dark:text-slate-400',
  },
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

interface Props {
  messages: Message[]
  isRecording: boolean
  isProcessing?: boolean
  playingMessageId?: string | null
  isMessageLoading?: boolean
  onPlayMessage?: (id: string, text: string) => void
  onStopMessage?: () => void
  onTranslateMessage?: (
    id: string,
    text: string,
    speaker: string,
    detectedLanguage?: 'ko' | 'en'
  ) => void
  pendingTranscript?: {
    messageId: string
    text: string
    speaker: string
    translation?: string
  } | null
}

export default function VoiceArea({
  messages,
  isRecording,
  isProcessing,
  playingMessageId,
  isMessageLoading,
  onPlayMessage,
  onStopMessage,
  onTranslateMessage,
  pendingTranscript,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  const voiceMessages = messages.filter(
    (m): m is Message & { speaker: 'speaker1' | 'speaker2' } =>
      m.speaker === 'speaker1' || m.speaker === 'speaker2'
  )

  const showPending =
    isRecording &&
    pendingTranscript &&
    pendingTranscript.text &&
    (pendingTranscript.speaker === 'speaker1' || pendingTranscript.speaker === 'speaker2')

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    requestAnimationFrame(() => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom < 200) {
        el.scrollTop = el.scrollHeight
      }
    })
  }, [voiceMessages.length, pendingTranscript])

  if (voiceMessages.length === 0 && !showPending) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 min-h-0 text-center px-8 select-none">
        <div
          className={`w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 transition-colors ${
            isRecording
              ? 'bg-red-100/80 dark:bg-red-900/30 text-red-500'
              : 'bg-slate-100/80 dark:bg-slate-800/60 text-slate-400 dark:text-slate-500'
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-7 h-7"
          >
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" x2="12" y1="19" y2="22" />
          </svg>
        </div>
        <p className="text-sm text-slate-400 dark:text-slate-500 leading-relaxed">
          {isRecording
            ? '음성을 인식하고 있습니다...'
            : '녹음을 시작하면\n대화 내용이 여기에 표시됩니다'}
        </p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0 overflow-y-auto scrollbar-thin px-4 py-3 space-y-2"
    >
      {voiceMessages.map((msg) => {
        const cfg = SPEAKER_CONFIG[msg.speaker]
        const isPlaying = playingMessageId === msg.id
        const isTranslating = msg.streamPhase === 'translating' || msg.streamPhase === 'stt'

        return (
          <div
            key={msg.id}
            onClick={() =>
              onTranslateMessage?.(msg.id, msg.original, msg.speaker, msg.detectedLanguage)
            }
            className={`group relative rounded-xl cursor-pointer transition-opacity hover:opacity-90 active:opacity-70 ${cfg.cardBg}`}
          >
            <div className="px-4 py-3">
              {/* 헤더: 화자명 · 시간 */}
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-1.5">
                  <span className={`text-xs font-semibold ${cfg.nameColor}`}>{cfg.label}</span>
                  <span className="text-[10px] text-slate-300 dark:text-slate-600 select-none">
                    ·
                  </span>
                  <span className="text-[10px] text-slate-400 dark:text-slate-500 tabular-nums">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  {/* TTS 재생 버튼 */}
                  {msg.original && !isTranslating && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        isPlaying ? onStopMessage?.() : onPlayMessage?.(msg.id, msg.original)
                      }}
                      title={
                        isPlaying
                          ? isMessageLoading
                            ? '로딩 중...'
                            : '재생 중지'
                          : '번역 음성 재생'
                      }
                      className={`flex items-center justify-center w-5 h-5 rounded-full transition-all ${
                        isPlaying
                          ? 'bg-red-100 dark:bg-red-900/30 text-red-500'
                          : 'opacity-0 group-hover:opacity-100 bg-slate-100/80 dark:bg-white/8 text-slate-400 dark:text-slate-500 hover:bg-slate-200 dark:hover:bg-white/12'
                      }`}
                    >
                      {isPlaying ? (
                        isMessageLoading ? (
                          <svg
                            className="w-2.5 h-2.5 animate-spin"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                          </svg>
                        ) : (
                          <svg viewBox="0 0 24 24" fill="currentColor" className="w-2 h-2">
                            <rect x="6" y="6" width="12" height="12" rx="1" />
                          </svg>
                        )
                      ) : (
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="w-2.5 h-2.5"
                        >
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                          <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              </div>

              {/* 원문 */}
              <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                {msg.original}
                {isTranslating && !msg.translation && (
                  <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                )}
              </p>

              {/* 번역 */}
              {(msg.translation || isTranslating) && (
                <p className={`mt-1.5 text-sm leading-relaxed ${cfg.translationColor}`}>
                  {msg.translation}
                  {isTranslating && (
                    <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                  )}
                </p>
              )}
            </div>
          </div>
        )
      })}

      {/* Pending 카드 — 실시간 부분 인식 */}
      {showPending &&
        (() => {
          const sp = pendingTranscript!.speaker as 'speaker1' | 'speaker2'
          const cfg = SPEAKER_CONFIG[sp] ?? SPEAKER_CONFIG.speaker1
          return (
            <div className="relative rounded-xl border border-dashed border-slate-300 dark:border-slate-700 bg-white/40 dark:bg-slate-800/30">
              <div className="px-4 py-3">
                <div className="mb-1.5">
                  <span className={`text-xs font-semibold ${cfg.nameColor} opacity-60`}>
                    {cfg.label}
                  </span>
                </div>
                <p className="text-sm text-slate-500 dark:text-slate-400 italic leading-relaxed">
                  {pendingTranscript!.text}
                  <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                </p>
                {pendingTranscript!.translation && (
                  <p
                    className={`mt-1.5 text-sm leading-relaxed ${cfg.translationColor} opacity-80`}
                  >
                    {pendingTranscript!.translation}
                    <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                  </p>
                )}
              </div>
            </div>
          )
        })()}

      {/* 녹음 인디케이터 */}
      {isRecording && (
        <div className="flex items-center gap-2 px-3 py-2 mx-1 rounded-xl bg-red-50/60 dark:bg-red-900/15 border border-red-100/60 dark:border-red-500/10 w-fit">
          <span className="flex gap-[3px] items-end h-3">
            {[0, 0.15, 0.3].map((d, i) => (
              <span
                key={i}
                className="w-[3px] rounded-full bg-red-400 dark:bg-red-500 animate-bounce"
                style={{ animationDelay: `${d}s`, height: i === 1 ? '12px' : '8px' }}
              />
            ))}
          </span>
          <span className="text-[11px] text-red-500 dark:text-red-400 font-medium">
            {isProcessing ? '번역 중...' : '음성 인식 중...'}
          </span>
        </div>
      )}
    </div>
  )
}
