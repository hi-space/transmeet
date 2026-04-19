'use client'

import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { Message } from '@/types/meeting'

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

interface Props {
  messages: Message[]
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
  playingMessageId,
  isMessageLoading,
  onPlayMessage,
  onStopMessage,
  onTranslateMessage,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  const myMessages = messages.filter((m) => m.speaker === 'me')

  const streamingQaLength = myMessages.find((m) => m.qaStreamPhase === 'streaming')?.qaResponse
    ?.length

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    requestAnimationFrame(() => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom < 200) {
        el.scrollTop = el.scrollHeight
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myMessages.length, streamingQaLength])

  if (myMessages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 min-h-0 text-center px-8 select-none">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4 bg-slate-100/80 dark:bg-slate-800/60 text-slate-400 dark:text-slate-500">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-7 h-7"
          >
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
          </svg>
        </div>
        <p className="text-sm text-slate-400 dark:text-slate-500 leading-relaxed">
          한글로 입력하면
          <br />
          번역된 내용이 여기에 표시됩니다
        </p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0 overflow-y-auto scrollbar-thin px-4 py-3 space-y-3"
    >
      {myMessages.map((msg) => {
        const isPlaying = playingMessageId === msg.id
        const isTranslating = msg.streamPhase === 'translating' || msg.streamPhase === 'stt'
        const isQaStreaming = msg.qaStreamPhase === 'streaming'
        const hasQa = !!msg.qaResponse

        return (
          <div key={msg.id}>
            {/* 타임스탬프 — 카드 바깥 상단 */}
            <span className="block text-[10px] text-slate-400 dark:text-slate-500 tabular-nums mb-1 px-1">
              {formatTime(msg.timestamp)}
            </span>

            <div
              className="group relative rounded-xl px-4 py-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-800 cursor-pointer transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-700 active:opacity-70"
              onClick={() =>
                onTranslateMessage?.(msg.id, msg.translation, msg.speaker, msg.detectedLanguage)
              }
              title="클릭하여 재번역"
            >
              {/* 한글 원문 */}
              <p className="text-sm text-slate-700 dark:text-slate-200 leading-relaxed">
                {msg.translation || (
                  <span className="text-slate-400 dark:text-slate-500 italic text-xs">
                    번역 중...
                  </span>
                )}
              </p>

              {/* 영어 번역 */}
              <p className="mt-1.5 text-sm text-cyan-600 dark:text-cyan-500 leading-relaxed">
                {msg.original}
                {isTranslating && (
                  <span className="inline-block w-[2px] h-[0.7em] bg-current ml-[2px] align-middle animate-pulse" />
                )}
              </p>

              {/* TTS 재생 버튼 */}
              {msg.original && !isTranslating && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    isPlaying ? onStopMessage?.() : onPlayMessage?.(msg.id, msg.original)
                  }}
                  title={
                    isPlaying ? (isMessageLoading ? '로딩 중...' : '재생 중지') : '영어로 읽기'
                  }
                  className={`absolute right-3 top-3 flex items-center justify-center w-6 h-6 rounded-full transition-all ${
                    isPlaying
                      ? 'bg-red-100 dark:bg-red-900/30 text-red-500'
                      : 'opacity-0 group-hover:opacity-100 bg-slate-100/80 dark:bg-white/8 text-slate-400 dark:text-slate-500 hover:bg-slate-200 dark:hover:bg-white/12 hover:text-slate-600 dark:hover:text-slate-300'
                  }`}
                >
                  {isPlaying ? (
                    isMessageLoading ? (
                      <svg
                        className="w-3 h-3 animate-spin"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 24 24" fill="currentColor" className="w-2.5 h-2.5">
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
                      className="w-3 h-3"
                    >
                      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                    </svg>
                  )}
                </button>
              )}
            </div>

            {/* Q&A 응답 */}
            {hasQa && (
              <div
                className={`mt-1.5 mx-1 rounded-lg px-3 py-2 text-xs leading-relaxed ${
                  msg.qaStreamPhase === 'error'
                    ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800'
                    : 'bg-indigo-50 dark:bg-indigo-900/20 text-slate-700 dark:text-slate-300 border border-indigo-100 dark:border-indigo-800'
                }`}
              >
                <div className="prose-sm prose-slate dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                  <ReactMarkdown>{msg.qaResponse ?? ''}</ReactMarkdown>
                  {isQaStreaming && (
                    <span className="inline-block w-[2px] h-[0.7em] bg-indigo-500 ml-[2px] align-middle animate-pulse" />
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
