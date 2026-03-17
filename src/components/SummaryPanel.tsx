'use client'

interface Props {
  summary: string[]
  onClose?: () => void
  onSummarize?: () => void
  isSummarizing?: boolean
}

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
          {/* Summarize button */}
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
        {isSummarizing && summary.length === 0 ? (
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
        ) : summary.length === 0 ? (
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
        ) : (
          <ul className="space-y-3">
            {summary.map((point, i) => (
              <li key={i} className="flex items-start gap-2.5">
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-[10px] font-bold mt-0.5">
                  {i + 1}
                </span>
                <span className="text-sm text-slate-600 dark:text-slate-300 leading-relaxed">
                  {point}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
