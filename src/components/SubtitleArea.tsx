'use client'

export default function SubtitleArea() {
  return (
    <div className="flex flex-col h-full p-4 gap-4 overflow-hidden">
      {/* Original transcript section */}
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            원문
          </span>
          <span className="text-xs text-slate-400 dark:text-slate-600">EN</span>
        </div>
        <div className="flex-1 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 overflow-y-auto scrollbar-thin">
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="w-12 h-12 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center mx-auto mb-3">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-6 h-6 text-slate-400 dark:text-slate-500"
                >
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                </svg>
              </div>
              <p className="text-sm text-slate-400 dark:text-slate-500">
                녹음을 시작하면 영어 원문이 표시됩니다
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Translation section */}
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-500">
            번역
          </span>
          <span className="text-xs text-slate-400 dark:text-slate-600">KO</span>
        </div>
        <div className="flex-1 rounded-xl border border-brand-100 dark:border-slate-700 bg-brand-50 dark:bg-slate-800 p-4 overflow-y-auto scrollbar-thin">
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="w-12 h-12 rounded-full bg-brand-100 dark:bg-slate-700 flex items-center justify-center mx-auto mb-3">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-6 h-6 text-brand-400 dark:text-slate-500"
                >
                  <path d="m5 8 6 6" />
                  <path d="m4 14 6-6 2-3" />
                  <path d="M2 5h12" />
                  <path d="M7 2h1" />
                  <path d="m22 22-5-10-5 10" />
                  <path d="M14 18h6" />
                </svg>
              </div>
              <p className="text-sm text-brand-400 dark:text-slate-500">
                실시간 한글 번역이 여기에 표시됩니다
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
