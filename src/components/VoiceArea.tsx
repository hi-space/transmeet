'use client'

import { useEffect, useRef } from 'react'
import { Message } from '@/types/meeting'
import Icon from '@/components/ui/Icon'
import { getSpeakerLabel, isVoiceSpeaker, normalizeSpeaker } from '@/lib/speakers'

const SPEAKER_COLORS = [
  {
    color: 'text-accent-700 dark:text-accent-300',
    dot: 'bg-accent-500',
  },
  { color: 'text-amber-700 dark:text-amber-300', dot: 'bg-amber-500' },
  { color: 'text-violet-700 dark:text-violet-300', dot: 'bg-violet-500' },
  { color: 'text-emerald-700 dark:text-emerald-300', dot: 'bg-emerald-500' },
  { color: 'text-rose-700 dark:text-rose-300', dot: 'bg-rose-500' },
]

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
    time: 'text-[0.6875rem]',
    original: 'text-sm leading-reading',
    translation: 'mt-2 text-sm leading-reading font-medium',
    btn: 'w-7 h-7',
    btnIcon: 'w-3.5 h-3.5',
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
    original: 'text-xl sm:text-2xl leading-reading sm:leading-reading font-medium',
    translation: 'mt-2 text-lg sm:text-xl leading-reading sm:leading-reading',
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
    original: 'text-3xl sm:text-4xl leading-reading sm:leading-reading font-semibold',
    translation: 'mt-3 text-2xl sm:text-3xl leading-reading sm:leading-reading',
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
    original: 'text-5xl sm:text-6xl leading-reading sm:leading-reading font-bold',
    translation: 'mt-5 text-3xl sm:text-4xl leading-reading sm:leading-reading',
    btn: 'w-12 h-12',
    btnIcon: 'w-6 h-6',
    btnStop: 'w-5 h-5',
    caret: 'w-[5px] h-[0.75em] ml-[5px]',
    autoScrollThreshold: 900,
  },
}

const FONT_SIZE_LABELS: Record<VoiceFontSize, string> = {
  compact: '작게',
  md: '보통',
  lg: '크게',
  xl: '아주 크게',
}

export function VoiceFontSizeControl({
  fontSize,
  onChange,
}: {
  fontSize: VoiceFontSize
  onChange: (size: VoiceFontSize) => void
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted">
      <span aria-hidden="true" className="font-medium tracking-tighter">
        Aa
      </span>
      <select
        aria-label="대화 글자 크기"
        value={fontSize}
        onChange={(event) => onChange(event.target.value as VoiceFontSize)}
        className="min-h-8 cursor-pointer rounded-md border border-transparent bg-transparent py-1 pl-1 pr-5 text-xs text-muted hover:bg-slate-100 dark:hover:bg-slate-800"
      >
        {Object.entries(FONT_SIZE_LABELS).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </label>
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
  isVisible?: boolean
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
  isVisible = true,
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
  const followLatest = useRef(true)
  const t = SIZE_TOKENS[fontSize] ?? SIZE_TOKENS.compact
  const voiceMessages = messages.filter((message) => isVoiceSpeaker(message.speaker))
  const showPending =
    isRecording && pendingTranscript?.text && isVoiceSpeaker(pendingTranscript.speaker)
  const latest = voiceMessages.at(-1)

  useEffect(() => {
    if (!isVisible) return
    const frame = requestAnimationFrame(() => {
      if (containerRef.current && followLatest.current)
        containerRef.current.scrollTop = containerRef.current.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [
    isVisible,
    voiceMessages.length,
    latest?.original,
    latest?.translation,
    pendingTranscript,
    fontSize,
  ])

  if (voiceMessages.length === 0 && !showPending) {
    return (
      <div className="ui-empty" role="status">
        <div
          className={`ui-empty-icon ${isRecording ? '!bg-red-50 !text-red-500 dark:!bg-red-500/10' : ''}`}
        >
          <Icon name={isRecording ? 'waveform' : 'mic'} className="h-6 w-6" />
        </div>
        <p className="text-sm font-medium text-ink">
          {isRecording ? '음성을 듣고 있습니다' : '대화를 시작해 보세요'}
        </p>
        <p className="text-xs">
          {isRecording ? '인식한 내용이 여기에 표시됩니다' : '녹음 시작을 누르면 대화가 번역됩니다'}
        </p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="scrollbar-thin min-h-0 flex-1 overflow-y-auto bg-subtle"
      onScroll={(event) => {
        const element = event.currentTarget
        followLatest.current =
          element.scrollHeight - element.scrollTop - element.clientHeight < t.autoScrollThreshold
      }}
    >
      <div className={t.container}>
        {voiceMessages.map((message) => {
          const speaker = {
            ...SPEAKER_COLORS[(Number(message.speaker.slice(7)) - 1) % SPEAKER_COLORS.length],
            label: getSpeakerLabel(message.speaker),
          }
          const playing = playingMessageId === message.id
          const translating = message.streamPhase === 'translating' || message.streamPhase === 'stt'
          const latestActive = isRecording && !showPending && message.id === latest?.id
          return (
            <article
              key={message.id}
              className={`border bg-panel ${t.card} ${latestActive ? 'border-accent-300 dark:border-accent-600' : 'border-subtle'}`}
            >
              <div className={t.inner}>
                <div className={`flex items-center justify-between gap-2 ${t.header}`}>
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${speaker.dot}`} />
                    <span className={`font-semibold ${t.label} ${speaker.color}`}>
                      {speaker.label}
                    </span>
                    <time
                      className={`${t.time} tabular-nums text-muted`}
                      dateTime={message.timestamp}
                    >
                      {formatTime(message.timestamp)}
                    </time>
                    {latestActive && (
                      <span className="text-[0.6875rem] text-accent-600 dark:text-accent-400">
                        실시간
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5">
                    {onTranslateMessage && message.original && (
                      <button
                        type="button"
                        onClick={() =>
                          onTranslateMessage(
                            message.id,
                            message.original,
                            message.speaker,
                            message.detectedLanguage
                          )
                        }
                        disabled={translating}
                        aria-label={`${speaker.label} ${formatTime(message.timestamp)} ${message.translation ? '다시 번역' : '번역'}`}
                        title={message.translation ? '다시 번역' : '번역'}
                        className="ui-icon-button h-7 w-7 min-h-7 p-1"
                      >
                        <Icon
                          name={translating ? 'loader' : 'languages'}
                          className={`h-3.5 w-3.5 ${translating ? 'animate-spin' : ''}`}
                        />
                      </button>
                    )}
                    {message.original && !translating && onPlayMessage && (
                      <button
                        type="button"
                        onClick={() =>
                          playing ? onStopMessage?.() : onPlayMessage(message.id, message.original)
                        }
                        aria-label={
                          playing
                            ? '음성 재생 중지'
                            : `${speaker.label} ${formatTime(message.timestamp)} 원문 음성 재생`
                        }
                        title={playing ? '음성 재생 중지' : '원문 음성 재생'}
                        className={`ui-icon-button min-h-7 p-1 ${t.btn} ${playing ? 'text-red-500' : ''}`}
                      >
                        <Icon
                          name={playing ? (isMessageLoading ? 'loader' : 'stop') : 'volume'}
                          className={`${t.btnIcon} ${playing && isMessageLoading ? 'animate-spin' : ''}`}
                        />
                      </button>
                    )}
                  </div>
                </div>
                <p className={`${t.original} break-words text-muted`}>
                  {message.original}
                  {translating && !message.translation && (
                    <span
                      className={`inline-block animate-pulse bg-current align-middle ${t.caret}`}
                    />
                  )}
                </p>
                {(message.translation || translating) && (
                  <p className={`${t.translation} break-words text-ink`}>
                    {message.translation}
                    {translating && (
                      <span
                        className={`inline-block animate-pulse bg-accent-500 align-middle ${t.caret}`}
                      />
                    )}
                  </p>
                )}
              </div>
            </article>
          )
        })}

        {showPending && pendingTranscript && (
          <article
            className={`border border-dashed border-accent-300 bg-panel dark:border-accent-700 ${t.card}`}
          >
            <div className={t.inner}>
              <div className={`flex items-center gap-2 ${t.header}`}>
                <span className="h-1.5 w-1.5 rounded-full bg-accent-500" />
                <span className={`${t.label} font-semibold text-accent-700 dark:text-accent-300`}>
                  {getSpeakerLabel(normalizeSpeaker(pendingTranscript.speaker))}
                </span>
                <span className="text-[0.6875rem] text-muted">듣는 중</span>
              </div>
              <p className={`${t.original} break-words text-muted`}>
                {pendingTranscript.text}
                <span className={`inline-block animate-pulse bg-current align-middle ${t.caret}`} />
              </p>
              {pendingTranscript.translation && (
                <p className={`${t.translation} break-words text-ink`}>
                  {pendingTranscript.translation}
                </p>
              )}
            </div>
          </article>
        )}
        {isRecording && (
          <div role="status" className="flex items-center gap-2 px-1 py-2 text-xs text-muted">
            <span className="blink-dot h-1.5 w-1.5 rounded-full bg-red-500" />
            {isProcessing ? '번역 중…' : '음성을 듣고 있습니다'}
          </div>
        )}
      </div>
    </div>
  )
}
