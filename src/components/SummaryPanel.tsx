'use client'

interface Props {
  summary: string[]
  onClose?: () => void
}

export default function SummaryPanel({ summary, onClose }: Props) {
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

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {summary.length === 0 ? (
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
              회의 후 요약 버튼을 눌러주세요.
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
