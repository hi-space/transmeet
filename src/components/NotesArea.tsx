'use client'

import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Message } from '@/types/meeting'
import Icon from '@/components/ui/Icon'

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
}

export default function NotesArea({
  messages,
  isVisible = true,
  playingMessageId,
  isMessageLoading,
  onPlayMessage,
  onStopMessage,
  onTranslateMessage,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const myMessages = messages.filter((message) => message.speaker === 'me')
  const last = myMessages.at(-1)
  const streamingQa = myMessages.find(
    (message) => message.qaStreamPhase === 'streaming'
  )?.qaResponse

  useEffect(() => {
    if (!isVisible) return
    const frame = requestAnimationFrame(() => {
      if (containerRef.current && followLatest.current)
        containerRef.current.scrollTop = containerRef.current.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [isVisible, myMessages.length, last?.original, last?.translation, streamingQa])

  if (!myMessages.length) {
    return (
      <div className="scrollbar-thin flex min-h-0 flex-1 overflow-y-auto">
        <div className="m-auto flex w-full shrink-0 flex-col items-center gap-2 p-5 text-center text-muted">
          <div className="ui-empty-icon">
            <Icon name="notes" className="h-6 w-6" />
          </div>
          <p className="text-sm font-medium text-ink">내가 입력한 내용</p>
          <p className="text-xs">아래 입력창에서 메시지를 보내세요</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="scrollbar-thin min-h-0 flex-1 space-y-3 overflow-y-auto bg-subtle p-3"
      onScroll={(event) => {
        const element = event.currentTarget
        followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 200
      }}
    >
      {myMessages.map((message) => {
        const playing = playingMessageId === message.id
        const translating = message.streamPhase === 'translating' || message.streamPhase === 'stt'
        return (
          <article key={message.id} className="rounded-xl border border-subtle bg-panel p-3.5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-accent-700 dark:text-accent-300">
                  나
                </span>
                <time
                  dateTime={message.timestamp}
                  className="text-[0.6875rem] tabular-nums text-muted"
                >
                  {formatTime(message.timestamp)}
                </time>
              </div>
              <div className="flex gap-0.5">
                {onTranslateMessage && (
                  <button
                    type="button"
                    onClick={() =>
                      onTranslateMessage(
                        message.id,
                        message.translation,
                        message.speaker,
                        message.detectedLanguage
                      )
                    }
                    disabled={translating || !message.translation}
                    aria-label="내 메시지 다시 번역"
                    title="다시 번역"
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
                    aria-label={playing ? '음성 재생 중지' : '내 메시지 음성 재생'}
                    title={playing ? '음성 재생 중지' : '번역 음성 재생'}
                    className={`ui-icon-button h-7 w-7 min-h-7 p-1 ${playing ? 'text-red-500' : ''}`}
                  >
                    <Icon
                      name={playing ? (isMessageLoading ? 'loader' : 'stop') : 'volume'}
                      className={`h-3.5 w-3.5 ${playing && isMessageLoading ? 'animate-spin' : ''}`}
                    />
                  </button>
                )}
              </div>
            </div>
            <p className="break-words text-sm leading-reading text-ink">
              {message.translation || '번역 중…'}
            </p>
            <p className="mt-2 break-words text-sm leading-reading text-muted">
              {message.original}
              {translating && (
                <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse bg-accent-500 align-middle" />
              )}
            </p>
            {message.qaResponse && (
              <div
                className={`mt-3 rounded-lg border p-3 ${message.qaStreamPhase === 'error' ? 'border-red-200 bg-red-50 text-red-600 dark:border-red-900 dark:bg-red-500/10 dark:text-red-400' : 'border-subtle bg-subtle'}`}
              >
                <div className="prose max-w-none text-sm leading-reading dark:prose-invert [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.qaResponse}</ReactMarkdown>
                  {message.qaStreamPhase === 'streaming' && (
                    <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse bg-accent-500 align-middle" />
                  )}
                </div>
              </div>
            )}
          </article>
        )
      })}
      <div aria-hidden="true" className="h-24 lg:hidden" />
    </div>
  )
}
