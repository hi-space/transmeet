'use client'

import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Message } from '@/types/meeting'
import Icon from '@/components/ui/Icon'
import { getSpeakerLabel } from '@/lib/speakers'

interface Props {
  summary?: string
  messages?: Message[]
  meetingTitle?: string
  onClose?: () => void
  onSummarize?: () => void
  isSummarizing?: boolean
}

// ─── Conversation serialization ──────────────────────────────────────────────
//
// Turns the structured Message[] into a plain-text transcript for the clipboard.
// Emits a timestamped, original-language transcript: one line per turn, with a
// [HH:MM] timestamp and the speaker label.
function formatConversation(messages: Message[], meetingTitle?: string): string {
  const header = meetingTitle ? `# ${meetingTitle}\n\n` : ''
  const body = messages
    .map((msg) => {
      const time = new Date(msg.timestamp).toLocaleTimeString('ko-KR', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
      const label = getSpeakerLabel(msg.speaker, 'en')
      return `[${time}] ${label}: ${msg.original?.trim() ?? ''}`
    })
    .join('\n')
  return header + body
}

export default function SummaryPanel({
  summary,
  messages,
  meetingTitle,
  onClose,
  onSummarize,
  isSummarizing,
}: Props) {
  const [copied, setCopied] = useState<'summary' | 'conversation' | null>(null)
  const [copyError, setCopyError] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasMessages = !!messages?.length

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    []
  )

  const copy = async (kind: 'summary' | 'conversation') => {
    const text = kind === 'summary' ? summary : formatConversation(messages ?? [], meetingTitle)
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(kind)
      setCopyError(false)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(null), 2000)
    } catch {
      setCopyError(true)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-panel">
      {(hasMessages || summary || onClose) && (
        <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-1 border-b border-subtle px-2.5 py-1.5">
          {hasMessages && (
            <button
              type="button"
              onClick={() => copy('conversation')}
              className="ui-button gap-1.5 px-2"
              title="전체 대화 복사"
            >
              <Icon name={copied === 'conversation' ? 'check' : 'copy'} className="h-3.5 w-3.5" />
              {copied === 'conversation' ? '복사됨' : '대화 복사'}
            </button>
          )}
          {summary && (
            <button
              type="button"
              onClick={() => copy('summary')}
              className="ui-button gap-1.5 px-2"
              title="요약 복사"
            >
              <Icon name={copied === 'summary' ? 'check' : 'summary'} className="h-3.5 w-3.5" />
              {copied === 'summary' ? '복사됨' : '요약 복사'}
            </button>
          )}
          {onSummarize && (summary || isSummarizing) && (
            <button
              type="button"
              onClick={onSummarize}
              disabled={!hasMessages}
              className="ui-icon-button ml-auto h-8 w-8 min-h-8"
              aria-label={isSummarizing ? '요약 다시 시도' : '요약 새로 생성'}
              title={isSummarizing ? '요약 다시 시도' : '요약 새로 생성'}
            >
              <Icon
                name="refresh"
                className={`h-3.5 w-3.5 ${isSummarizing ? 'animate-spin' : ''}`}
              />
            </button>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="요약 닫기"
              className="ui-icon-button"
            >
              <Icon name="close" />
            </button>
          )}
        </div>
      )}
      {copyError && (
        <p role="status" className="px-4 py-2 text-xs text-red-600 dark:text-red-400">
          복사하지 못했습니다. 다시 시도해 주세요.
        </p>
      )}
      {copied && (
        <span className="sr-only" role="status">
          복사했습니다
        </span>
      )}
      {summary ? (
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-4">
          <div className="mb-4 flex items-center gap-1.5 text-[0.6875rem] font-medium text-muted">
            <Icon name="sparkles" className="h-3.5 w-3.5 text-accent-600 dark:text-accent-400" />
            {isSummarizing ? '요약 업데이트 중' : '회의 요약'}
          </div>
          <div className="prose max-w-none break-words text-sm leading-reading dark:prose-invert [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 prose-strong:text-slate-800 dark:prose-strong:text-slate-100">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
            {isSummarizing && (
              <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse rounded-sm bg-accent-500" />
            )}
          </div>
        </div>
      ) : isSummarizing ? (
        <div className="ui-empty" role="status">
          <div className="ui-empty-icon">
            <Icon name="loader" className="h-6 w-6 animate-spin" />
          </div>
          <p className="text-sm font-medium text-ink">회의를 정리하고 있습니다</p>
          <p className="text-xs">요약이 생성되면 여기에 표시됩니다</p>
        </div>
      ) : (
        <div className="ui-empty">
          <div className="ui-empty-icon">
            <Icon name="summary" className="h-6 w-6" />
          </div>
          <p className="text-sm font-medium text-ink">아직 요약이 없습니다</p>
          <p className="text-xs">
            {hasMessages ? '대화에서 핵심 내용을 정리하세요' : '대화가 쌓이면 요약할 수 있습니다'}
          </p>
          {onSummarize && (
            <button
              type="button"
              onClick={onSummarize}
              disabled={!hasMessages}
              className="ui-button ui-button-primary mt-3"
            >
              <Icon name="sparkles" />
              요약 생성
            </button>
          )}
        </div>
      )}
    </div>
  )
}
