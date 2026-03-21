'use client'

import { useEffect, useRef } from 'react'
import { Message, SpeakerRole } from '@/types/meeting'

function SpeakerIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
    </svg>
  )
}

function StopIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
      <rect x="6" y="6" width="12" height="12" rx="1" />
    </svg>
  )
}

function LanguagesIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="m5 8 6 6" />
      <path d="m4 14 6-6 2-3" />
      <path d="M2 5h12" />
      <path d="M7 2h1" />
      <path d="m22 22-5-10-5 10" />
      <path d="M14 18h6" />
    </svg>
  )
}

const SPEAKER_CONFIG: Record<
  SpeakerRole,
  {
    label: string
    abbr: string
    side: 'left' | 'right'
    nameColor: string
    avatarBg: string
    avatarText: string
    bubbleBg: string
    translationColor: string
    dividerColor: string
  }
> = {
  speaker1: {
    label: 'Speaker 1',
    abbr: 'S1',
    side: 'left',
    nameColor: 'text-blue-600 dark:text-blue-400',
    avatarBg: 'bg-blue-100 dark:bg-blue-900/40',
    avatarText: 'text-blue-600 dark:text-blue-400',
    bubbleBg:
      'bg-white/75 dark:bg-slate-800/60 border border-slate-200/60 dark:border-white/8 shadow-sm shadow-slate-200/40 dark:shadow-black/20',
    translationColor: 'text-slate-500 dark:text-slate-400',
    dividerColor: 'border-slate-100/80 dark:border-white/6',
  },
  speaker2: {
    label: 'Speaker 2',
    abbr: 'S2',
    side: 'left',
    nameColor: 'text-emerald-600 dark:text-emerald-400',
    avatarBg: 'bg-emerald-100 dark:bg-emerald-900/40',
    avatarText: 'text-emerald-600 dark:text-emerald-400',
    bubbleBg:
      'bg-white/75 dark:bg-slate-800/60 border border-slate-200/60 dark:border-white/8 shadow-sm shadow-slate-200/40 dark:shadow-black/20',
    translationColor: 'text-slate-500 dark:text-slate-400',
    dividerColor: 'border-slate-100/80 dark:border-white/6',
  },
  me: {
    label: 'Me',
    abbr: 'Me',
    side: 'right',
    nameColor: 'text-indigo-600 dark:text-indigo-400',
    avatarBg: 'bg-indigo-100 dark:bg-indigo-900/40',
    avatarText: 'text-indigo-600 dark:text-indigo-400',
    bubbleBg:
      'bg-indigo-50/80 dark:bg-indigo-900/30 border border-indigo-200/50 dark:border-indigo-500/20 shadow-sm shadow-indigo-200/30 dark:shadow-indigo-900/20',
    translationColor: 'text-indigo-400 dark:text-indigo-500',
    dividerColor: 'border-indigo-100/80 dark:border-indigo-500/10',
  },
}

function PendingBubble({
  speaker,
  text,
  translation,
}: {
  speaker: string
  text: string
  translation?: string
}) {
  const speakerKey: SpeakerRole = speaker in SPEAKER_CONFIG ? (speaker as SpeakerRole) : 'speaker1'
  const cfg = SPEAKER_CONFIG[speakerKey]
  const isRight = cfg.side === 'right'
  return (
    <div
      className={`flex items-start gap-2.5 ${isRight ? 'flex-row-reverse ml-10 sm:ml-16' : 'mr-10 sm:mr-16'}`}
    >
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full ${cfg.avatarBg} ${cfg.avatarText} flex items-center justify-center text-[10px] font-bold opacity-60`}
      >
        {cfg.abbr}
      </div>
      <div className={`flex flex-col min-w-0 ${isRight ? 'items-end' : 'items-start'}`}>
        <div
          className={`rounded-2xl px-3.5 py-2.5 max-w-xs sm:max-w-sm border border-dashed border-slate-300/60 dark:border-slate-600/40 bg-white/40 dark:bg-slate-800/30 ${isRight ? 'rounded-tr-sm' : 'rounded-tl-sm'}`}
        >
          <p className="text-sm text-slate-500 dark:text-slate-400 italic leading-relaxed">
            {text}
            <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
          </p>
          {translation && (
            <div className={`border-t ${cfg.dividerColor} mt-1.5 pt-1.5`}>
              <p className={`text-xs leading-relaxed ${cfg.translationColor}`}>
                {translation}
                <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
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
  isProcessing?: boolean // true while a TTS message is being translated
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

export default function ChatArea({
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

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    requestAnimationFrame(() => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom < 200) {
        el.scrollTop = el.scrollHeight
      }
    })
  }, [messages, pendingTranscript])

  if (messages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-8 select-none">
        <div
          className={`w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 transition-colors ${
            isRecording
              ? 'bg-rose-100/80 dark:bg-rose-900/30 text-rose-500'
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
    <div ref={containerRef} className="h-full overflow-y-auto scrollbar-thin px-4 py-4 space-y-2">
      {messages.map((msg) => {
        const cfg = SPEAKER_CONFIG[msg.speaker]
        const isRight = cfg.side === 'right'

        return (
          <div
            key={msg.id}
            className={`group flex items-start gap-2.5 ${
              isRight ? 'flex-row-reverse ml-10 sm:ml-16' : 'mr-10 sm:mr-16'
            }`}
          >
            {/* Avatar */}
            <div
              className={`flex-shrink-0 w-8 h-8 rounded-full ${cfg.avatarBg} ${cfg.avatarText} flex items-center justify-center text-[10px] font-bold`}
            >
              {cfg.abbr}
            </div>

            {/* Bubble content */}
            <div className={`flex flex-col min-w-0 ${isRight ? 'items-end' : 'items-start'}`}>
              {/* Name + timestamp */}
              <div
                className={`flex items-baseline gap-1.5 mb-1 ${isRight ? 'flex-row-reverse' : ''}`}
              >
                <span className={`text-xs font-semibold ${cfg.nameColor}`}>{cfg.label}</span>
                <span className="text-[10px] text-slate-400 dark:text-slate-500">
                  {formatTime(msg.timestamp)}
                </span>
              </div>

              {/* Bubble + side buttons row */}
              <div className={`flex items-center gap-2 ${isRight ? 'flex-row-reverse' : ''}`}>
                {/* Message bubble */}
                <div
                  onClick={() => {
                    if (msg.streamPhase === 'translating' || msg.streamPhase === 'stt') return
                    // 'me': KO(translation) → EN / others: EN(original) → KO
                    const textToTranslate = msg.speaker === 'me' ? msg.translation : msg.original
                    onTranslateMessage?.(msg.id, textToTranslate, msg.speaker, msg.detectedLanguage)
                  }}
                  className={`rounded-2xl px-3.5 py-2.5 max-w-xs sm:max-w-sm ${cfg.bubbleBg} ${
                    isRight ? 'rounded-tr-sm' : 'rounded-tl-sm'
                  } cursor-pointer active:opacity-70 transition-opacity`}
                >
                  {/* Original text */}
                  <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                    {msg.original}
                  </p>
                  {/* Translation — three states */}
                  <div className={`border-t ${cfg.dividerColor} mt-1.5 pt-1.5`}>
                    {msg.streamPhase === 'stt' && !msg.translation ? (
                      // 번역 대기 중 (기존 번역 없음)
                      <span className="flex items-center gap-1">
                        <span className={`text-xs ${cfg.translationColor}`}>번역 중</span>
                        {[0, 0.1, 0.2].map((d, i) => (
                          <span
                            key={i}
                            className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-600 animate-bounce"
                            style={{ animationDelay: `${d}s` }}
                          />
                        ))}
                      </span>
                    ) : (
                      // 번역 있음 — 스트리밍 중이면 커서 표시
                      <p className={`text-xs leading-relaxed ${cfg.translationColor}`}>
                        {msg.translation}
                        {(msg.streamPhase === 'translating' ||
                          (msg.streamPhase === 'stt' && !!msg.translation)) && (
                          <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                        )}
                      </p>
                    )}
                  </div>
                </div>

                {/* Side action buttons */}
                {(msg.streamPhase !== 'stt' || msg.original) && (
                  <div className="flex flex-col justify-between self-stretch">
                    {/* TTS play/stop button */}
                    {msg.translation && msg.streamPhase !== 'translating' && (
                      <button
                        onClick={() =>
                          playingMessageId === msg.id
                            ? onStopMessage?.()
                            : onPlayMessage?.(msg.id, msg.original)
                        }
                        title={
                          playingMessageId === msg.id
                            ? isMessageLoading
                              ? '로딩 중...'
                              : '재생 중지'
                            : '번역 음성 재생'
                        }
                        className={`flex items-center justify-center w-5 h-5 rounded-full transition-colors ${
                          playingMessageId === msg.id
                            ? 'bg-rose-100 dark:bg-rose-900/30 text-rose-500 hover:bg-rose-200 dark:hover:bg-rose-900/50'
                            : 'opacity-0 group-hover:opacity-100 bg-slate-100/80 dark:bg-white/6 text-slate-400 dark:text-slate-500 hover:bg-slate-200 dark:hover:bg-white/10 hover:text-slate-600 dark:hover:text-slate-300'
                        }`}
                      >
                        {playingMessageId === msg.id ? (
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
                            <StopIcon className="w-2 h-2" />
                          )
                        ) : (
                          <SpeakerIcon className="w-2.5 h-2.5" />
                        )}
                      </button>
                    )}

                    {/* Translate button */}
                    <button
                      onClick={() =>
                        onTranslateMessage?.(
                          msg.id,
                          msg.original,
                          msg.speaker,
                          msg.detectedLanguage
                        )
                      }
                      title="번역"
                      className={`flex items-center justify-center w-5 h-5 rounded-full transition-all ${
                        msg.streamPhase === 'translating'
                          ? 'bg-indigo-100/80 dark:bg-indigo-900/30 text-indigo-400 dark:text-indigo-500'
                          : 'opacity-0 group-hover:opacity-100 bg-slate-100/80 dark:bg-white/6 text-slate-400 dark:text-slate-500 hover:bg-slate-200 dark:hover:bg-white/10 hover:text-slate-600 dark:hover:text-slate-300'
                      }`}
                    >
                      {msg.streamPhase === 'translating' ? (
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
                        <LanguagesIcon className="w-2.5 h-2.5" />
                      )}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })}
      {/* Pending Transcribe bubble: word-by-word partial transcript */}
      {isRecording && pendingTranscript && pendingTranscript.text && (
        <PendingBubble
          speaker={pendingTranscript.speaker}
          text={pendingTranscript.text}
          translation={pendingTranscript.translation}
        />
      )}

      {/* Recording indicator: shows while waiting for next subtitle */}
      {isRecording && (
        <div className="flex items-center gap-2 px-3 py-2 mx-2 mb-1 rounded-xl bg-rose-50/60 dark:bg-rose-900/15 border border-rose-100/60 dark:border-rose-500/10 w-fit">
          <span className="flex gap-[3px] items-end h-3">
            {[0, 0.15, 0.3].map((d, i) => (
              <span
                key={i}
                className="w-[3px] rounded-full bg-rose-400 dark:bg-rose-500 animate-bounce"
                style={{ animationDelay: `${d}s`, height: i === 1 ? '12px' : '8px' }}
              />
            ))}
          </span>
          <span className="text-[11px] text-rose-500 dark:text-rose-400 font-medium">
            {isProcessing ? '번역 중...' : '음성 인식 중...'}
          </span>
        </div>
      )}
    </div>
  )
}
