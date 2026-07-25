'use client'

import { useEffect, useRef } from 'react'
import { Message } from '@/types/meeting'

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
    nameColor: 'text-cyan-600 dark:text-cyan-400',
    cardBg:
      'bg-white/60 dark:bg-slate-800/60 glass border border-slate-200/50 dark:border-slate-700/50',
    translationColor: 'text-slate-500 dark:text-slate-400',
  },
  speaker2: {
    label: 'Speaker 2',
    nameColor: 'text-amber-600 dark:text-amber-400',
    cardBg:
      'bg-white/60 dark:bg-slate-800/60 glass border border-slate-200/50 dark:border-slate-700/50',
    translationColor: 'text-slate-500 dark:text-slate-400',
  },
}

// ─── 글자 크기 단계 ───────────────────────────────────────────────────────────
// compact: 회의 흐름을 훑는 기본 크기
// 그 이상은 화면을 다른 사람에게 보여줄 때 — 말풍선 하나가 크게 읽힌다

export type VoiceFontSize = 'compact' | 'md' | 'lg' | 'xl'

interface StyleTokens {
  container: string
  card: string
  inner: string
  header: string
  label: string
  time: string
  original: string
  translation: string
  btn: string
  btnIcon: string
  btnStop: string
  caret: string
  /** 자동 스크롤 "바닥 근처" 판정 거리 — 말풍선이 커지면 함께 넓어져야 한다 */
  autoScrollThreshold: number
}

const SIZE_TOKENS: Record<VoiceFontSize, StyleTokens> = {
  compact: {
    container: 'px-4 py-3 space-y-2',
    card: 'rounded-xl',
    inner: 'px-4 py-3',
    header: 'mb-1.5',
    label: 'text-xs',
    time: 'text-[10px]',
    original: 'text-sm leading-relaxed',
    translation: 'mt-1.5 text-sm leading-relaxed',
    btn: 'w-5 h-5',
    btnIcon: 'w-2.5 h-2.5',
    btnStop: 'w-2 h-2',
    caret: 'w-[2px] h-[0.7em] ml-[2px]',
    autoScrollThreshold: 200,
  },
  md: {
    container: 'px-4 sm:px-5 py-4 space-y-3',
    card: 'rounded-2xl',
    inner: 'px-5 py-4',
    header: 'mb-2',
    label: 'text-sm',
    time: 'text-xs',
    original: 'text-xl sm:text-2xl leading-snug font-medium',
    translation: 'mt-2 text-lg sm:text-xl leading-snug',
    btn: 'w-8 h-8',
    btnIcon: 'w-4 h-4',
    btnStop: 'w-3 h-3',
    caret: 'w-[3px] h-[0.75em] ml-[3px]',
    autoScrollThreshold: 400,
  },
  lg: {
    container: 'px-4 sm:px-6 py-5 space-y-4',
    card: 'rounded-2xl shadow-sm',
    inner: 'px-6 py-6',
    header: 'mb-3',
    label: 'text-base',
    time: 'text-sm',
    original: 'text-3xl sm:text-4xl leading-tight font-semibold',
    translation: 'mt-3 text-2xl sm:text-3xl leading-snug',
    btn: 'w-10 h-10',
    btnIcon: 'w-5 h-5',
    btnStop: 'w-4 h-4',
    caret: 'w-[4px] h-[0.75em] ml-[4px]',
    autoScrollThreshold: 600,
  },
  xl: {
    container: 'px-4 sm:px-6 py-6 space-y-5',
    card: 'rounded-3xl shadow-md',
    inner: 'px-6 sm:px-8 py-8',
    header: 'mb-4',
    label: 'text-lg',
    time: 'text-base',
    original: 'text-5xl sm:text-6xl leading-tight font-bold',
    translation: 'mt-5 text-3xl sm:text-4xl leading-snug',
    btn: 'w-12 h-12',
    btnIcon: 'w-6 h-6',
    btnStop: 'w-5 h-5',
    caret: 'w-[5px] h-[0.75em] ml-[5px]',
    autoScrollThreshold: 900,
  },
}

const FONT_SIZE_ORDER: VoiceFontSize[] = ['compact', 'md', 'lg', 'xl']
const FONT_SIZE_TITLE: Record<VoiceFontSize, string> = {
  compact: '작게 — 회의 흐름 훑어보기',
  md: '보통',
  lg: '크게 — 화면 공유용',
  xl: '아주 크게 — 화면 공유용',
}
// 버튼 자체의 글자 크기로 단계를 직관적으로 보여준다
const FONT_SIZE_BTN_TEXT: Record<VoiceFontSize, string> = {
  compact: 'text-[9px]',
  md: 'text-[11px]',
  lg: 'text-[13px]',
  xl: 'text-[15px]',
}

/**
 * 말풍선 글자 크기 선택기 — 음성 입력 헤더(내 메모 / 요약 버튼 옆)에 배치한다.
 * 발표 중 현장에서 바로 조절하는 값이라 설정 패널에 숨기지 않는다.
 */
export function VoiceFontSizeControl({
  fontSize,
  onChange,
}: {
  fontSize: VoiceFontSize
  onChange: (size: VoiceFontSize) => void
}) {
  return (
    <div
      className="flex items-center p-0.5 rounded-md bg-slate-100 dark:bg-slate-800/80"
      role="group"
      aria-label="말풍선 글자 크기"
    >
      {FONT_SIZE_ORDER.map((size) => {
        const active = fontSize === size
        return (
          <button
            key={size}
            onClick={() => onChange(size)}
            title={FONT_SIZE_TITLE[size]}
            aria-label={FONT_SIZE_TITLE[size]}
            aria-pressed={active}
            className={`w-5 h-5 flex items-center justify-center rounded font-bold leading-none transition-colors ${FONT_SIZE_BTN_TEXT[size]} ${
              active
                ? 'bg-white dark:bg-slate-700 text-cyan-700 dark:text-cyan-300 shadow-sm'
                : 'text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'
            }`}
          >
            A
          </button>
        )
      })}
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
  fontSize?: VoiceFontSize
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
  fontSize = 'compact',
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  const t = SIZE_TOKENS[fontSize] ?? SIZE_TOKENS.compact

  const voiceMessages = messages.filter(
    (m): m is Message & { speaker: 'speaker1' | 'speaker2' } =>
      m.speaker === 'speaker1' || m.speaker === 'speaker2'
  )

  const showPending =
    isRecording &&
    pendingTranscript &&
    pendingTranscript.text &&
    (pendingTranscript.speaker === 'speaker1' || pendingTranscript.speaker === 'speaker2')

  // 맨 아래 말풍선이 "지금 말하는 내용" — 강조해서 눈이 갈 곳을 하나로 만든다.
  // pending 버블이 떠 있으면 그쪽이 최신이므로 강조를 넘긴다.
  const latestId = showPending ? undefined : voiceMessages.at(-1)?.id

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    requestAnimationFrame(() => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom < t.autoScrollThreshold) {
        el.scrollTop = el.scrollHeight
      }
    })
  }, [voiceMessages.length, pendingTranscript, t.autoScrollThreshold])

  if (voiceMessages.length === 0 && !showPending) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
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
          <p className="text-sm text-slate-400 dark:text-slate-500 leading-relaxed whitespace-pre-line">
            {isRecording
              ? '음성을 인식하고 있습니다...'
              : '녹음을 시작하면\n대화 내용이 여기에 표시됩니다'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div ref={containerRef} className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
        <div className={t.container}>
          {voiceMessages.map((msg) => {
            const cfg = SPEAKER_CONFIG[msg.speaker]
            const isPlaying = playingMessageId === msg.id
            const isTranslating = msg.streamPhase === 'translating' || msg.streamPhase === 'stt'
            const isLatest = msg.id === latestId

            return (
              <div
                key={msg.id}
                onClick={() =>
                  onTranslateMessage?.(msg.id, msg.original, msg.speaker, msg.detectedLanguage)
                }
                className={`group relative cursor-pointer transition-all hover:opacity-100 active:opacity-70 ${t.card} ${cfg.cardBg} ${
                  isLatest ? 'ring-2 ring-cyan-400/50 dark:ring-cyan-500/40' : 'opacity-75'
                }`}
              >
                <div className={t.inner}>
                  {/* 헤더: 화자명 · 시간 */}
                  <div className={`flex items-center justify-between ${t.header}`}>
                    <div className="flex items-center gap-1.5">
                      <span className={`font-semibold ${t.label} ${cfg.nameColor}`}>
                        {cfg.label}
                      </span>
                      <span className={`${t.time} text-slate-300 dark:text-slate-600 select-none`}>
                        ·
                      </span>
                      <span className={`${t.time} text-slate-400 dark:text-slate-500 tabular-nums`}>
                        {formatTime(msg.timestamp)}
                      </span>
                    </div>
                    <div className="flex items-center gap-1">
                      {/* TTS 재생 버튼 — 화면 공유 중에도 찾기 쉽도록 항상 보인다 */}
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
                          className={`flex items-center justify-center rounded-full transition-all ${t.btn} ${
                            isPlaying
                              ? 'bg-red-100 dark:bg-red-900/30 text-red-500'
                              : 'bg-slate-100/80 dark:bg-white/8 text-slate-400 dark:text-slate-500 hover:bg-slate-200 dark:hover:bg-white/12 hover:text-slate-600 dark:hover:text-slate-300'
                          }`}
                        >
                          {isPlaying ? (
                            isMessageLoading ? (
                              <svg
                                className={`${t.btnIcon} animate-spin`}
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                              >
                                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                              </svg>
                            ) : (
                              <svg viewBox="0 0 24 24" fill="currentColor" className={t.btnStop}>
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
                              className={t.btnIcon}
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
                  <p className={`${t.original} text-slate-700 dark:text-slate-200`}>
                    {msg.original}
                    {isTranslating && !msg.translation && (
                      <span
                        className={`inline-block bg-current align-middle animate-pulse ${t.caret}`}
                      />
                    )}
                  </p>

                  {/* 번역 */}
                  {(msg.translation || isTranslating) && (
                    <p className={`${t.translation} ${cfg.translationColor}`}>
                      {msg.translation}
                      {isTranslating && (
                        <span
                          className={`inline-block bg-current align-middle animate-pulse ${t.caret}`}
                        />
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
                <div
                  className={`relative border border-dashed border-slate-300 dark:border-slate-700 bg-white/40 dark:bg-slate-800/30 ring-2 ring-cyan-400/30 dark:ring-cyan-500/25 ${t.card}`}
                >
                  <div className={t.inner}>
                    <div className={t.header}>
                      <span className={`font-semibold ${t.label} ${cfg.nameColor} opacity-60`}>
                        {cfg.label}
                      </span>
                    </div>
                    <p className={`${t.original} text-slate-500 dark:text-slate-400 italic`}>
                      {pendingTranscript!.text}
                      <span
                        className={`inline-block bg-current align-middle animate-pulse ${t.caret}`}
                      />
                    </p>
                    {pendingTranscript!.translation && (
                      <p className={`${t.translation} ${cfg.translationColor} opacity-80`}>
                        {pendingTranscript!.translation}
                        <span
                          className={`inline-block bg-current align-middle animate-pulse ${t.caret}`}
                        />
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
      </div>
    </div>
  )
}
