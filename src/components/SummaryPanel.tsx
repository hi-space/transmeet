'use client'

import type { ReactNode } from 'react'

interface Props {
  summary?: string // raw markdown
  onClose?: () => void
  onSummarize?: () => void
  isSummarizing?: boolean
}

// ─── Inline bold renderer ────────────────────────────────────────────────────

function renderInline(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i} className="font-semibold text-slate-700 dark:text-slate-200">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    )
  )
}

// ─── Markdown renderer ───────────────────────────────────────────────────────

function MarkdownSummary({ text }: { text: string }) {
  const lines = text.split('\n')

  return (
    <div className="space-y-0.5">
      {lines.map((line, i) => {
        const t = line.trim()

        if (!t) return <div key={i} className="h-2" />

        if (t === '---')
          return <hr key={i} className="my-3 border-slate-200/60 dark:border-white/8" />

        if (t.startsWith('## '))
          return (
            <h3
              key={i}
              className="text-[10px] font-bold text-indigo-600 dark:text-indigo-400 uppercase tracking-wider pt-4 pb-1 first:pt-0"
            >
              {t.slice(3)}
            </h3>
          )

        if (t.startsWith('### '))
          return (
            <h4 key={i} className="text-xs font-semibold text-slate-600 dark:text-slate-300 pt-2">
              {renderInline(t.slice(4))}
            </h4>
          )

        if (t.startsWith('- ') || t.startsWith('* '))
          return (
            <div key={i} className="flex items-start gap-2 py-0.5">
              <span className="flex-shrink-0 w-1 h-1 rounded-full bg-indigo-400 dark:bg-indigo-500 mt-[6px]" />
              <span className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed">
                {renderInline(t.slice(2))}
              </span>
            </div>
          )

        const numMatch = t.match(/^(\d+)\.\s+(.+)/)
        if (numMatch)
          return (
            <div key={i} className="flex items-start gap-2 py-0.5">
              <span className="flex-shrink-0 text-[10px] font-bold text-indigo-500 w-4 text-right mt-0.5">
                {numMatch[1]}.
              </span>
              <span className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed">
                {renderInline(numMatch[2])}
              </span>
            </div>
          )

        return (
          <p key={i} className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed">
            {renderInline(t)}
          </p>
        )
      })}
    </div>
  )
}

// ─── Panel ───────────────────────────────────────────────────────────────────

export default function SummaryPanel({ summary, onClose, onSummarize, isSummarizing }: Props) {
  return (
    <div className="flex flex-col h-full w-full glass-sidebar border-l border-slate-200/60 dark:border-indigo-500/10">
      {/* Header */}
      <div className="px-4 h-12 flex items-center justify-between flex-shrink-0 border-b border-slate-100/70 dark:border-white/5">
        <div className="flex items-center gap-2">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            className="w-4 h-4 text-indigo-500"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" x2="8" y1="13" y2="13" />
            <line x1="16" x2="8" y1="17" y2="17" />
          </svg>
          <span className="text-[11px] font-bold uppercase tracking-widest text-slate-500 dark:text-slate-400">
            요약
          </span>
        </div>

        <div className="flex items-center gap-1">
          {onSummarize && (
            <button
              onClick={onSummarize}
              disabled={isSummarizing}
              title="요약 생성"
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-100 dark:hover:bg-indigo-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {isSummarizing ? (
                <>
                  <svg
                    className="w-3 h-3 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                  </svg>
                  <span>생성 중</span>
                </>
              ) : (
                <>
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    className="w-3 h-3"
                  >
                    <path d="M12 3v3m0 12v3M4.22 4.22l2.12 2.12m11.32 11.32 2.12 2.12M3 12h3m12 0h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12" />
                  </svg>
                  <span>요약</span>
                </>
              )}
            </button>
          )}

          {onClose && (
            <button
              onClick={onClose}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/8 transition-colors"
              aria-label="닫기"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-4 h-4"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {isSummarizing && !summary ? (
          <div className="flex flex-col items-center justify-center h-full text-center select-none">
            <div className="w-10 h-10 rounded-xl bg-indigo-50 dark:bg-indigo-900/30 text-indigo-500 flex items-center justify-center mb-3">
              <svg
                className="w-5 h-5 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
            </div>
            <p className="text-xs text-slate-400 dark:text-slate-500">
              요약을 생성하고 있습니다...
            </p>
          </div>
        ) : summary ? (
          <MarkdownSummary text={summary} />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center select-none">
            <div className="w-10 h-10 rounded-xl bg-slate-100/80 dark:bg-slate-800/60 text-slate-400 flex items-center justify-center mb-3">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                className="w-5 h-5"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
            <p className="text-xs text-slate-400 dark:text-slate-500 leading-relaxed">
              요약이 없습니다.
              <br />
              위의 요약 버튼을 눌러주세요.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
