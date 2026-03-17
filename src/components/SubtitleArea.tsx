'use client'

export default function SubtitleArea() {
  return (
    <div className="flex flex-col h-full px-4 py-3 gap-3 overflow-hidden">
      {/* Original transcript — EN */}
      <div className="flex flex-col flex-1 min-h-0">
        <SectionLabel lang="EN" label="원문" color="slate" />
        <div className="glass-panel flex-1 min-h-0 rounded-2xl overflow-hidden shadow-sm shadow-slate-200/50 dark:shadow-black/30">
          <div className="h-full overflow-y-auto scrollbar-thin p-4">
            <EmptyState
              icon={
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-6 h-6"
                >
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                </svg>
              }
              message="녹음을 시작하면 영어 원문이 표시됩니다"
              iconBg="bg-slate-100/80 dark:bg-slate-800/60"
              iconColor="text-slate-400 dark:text-slate-500"
              textColor="text-slate-400 dark:text-slate-500"
            />
          </div>
        </div>
      </div>

      {/* Translation — KO */}
      <div className="flex flex-col flex-1 min-h-0">
        <SectionLabel lang="KO" label="번역" color="indigo" />
        <div
          className="flex-1 min-h-0 rounded-2xl overflow-hidden shadow-sm shadow-indigo-200/40 dark:shadow-indigo-900/20 relative"
          style={{
            backdropFilter: 'blur(16px) saturate(160%)',
            WebkitBackdropFilter: 'blur(16px) saturate(160%)',
            background: 'rgba(238, 242, 255, 0.6)',
            border: '1px solid rgba(199, 210, 254, 0.6)',
          }}
        >
          {/* Dark override via a pseudo-inner overlay */}
          <div
            className="absolute inset-0 rounded-2xl hidden dark:block"
            style={{
              background: 'rgba(16, 12, 50, 0.55)',
              border: '1px solid rgba(99, 102, 241, 0.2)',
            }}
          />
          <div className="relative h-full overflow-y-auto scrollbar-thin p-4">
            <EmptyState
              icon={
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-6 h-6"
                >
                  <path d="m5 8 6 6" />
                  <path d="m4 14 6-6 2-3" />
                  <path d="M2 5h12" />
                  <path d="M7 2h1" />
                  <path d="m22 22-5-10-5 10" />
                  <path d="M14 18h6" />
                </svg>
              }
              message="실시간 한글 번역이 여기에 표시됩니다"
              iconBg="bg-indigo-100/70 dark:bg-indigo-900/30"
              iconColor="text-indigo-400 dark:text-indigo-500"
              textColor="text-indigo-400 dark:text-indigo-500"
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function SectionLabel({
  lang,
  label,
  color,
}: {
  lang: string
  label: string
  color: 'slate' | 'indigo'
}) {
  const isIndigo = color === 'indigo'
  return (
    <div className="flex items-center gap-2 mb-1.5 px-1">
      <span
        className={`text-[11px] font-bold uppercase tracking-widest ${
          isIndigo ? 'text-indigo-500 dark:text-indigo-400' : 'text-slate-400 dark:text-slate-500'
        }`}
      >
        {label}
      </span>
      <span
        className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-md tracking-wide ${
          isIndigo
            ? 'bg-indigo-100/80 dark:bg-indigo-900/40 text-indigo-500 dark:text-indigo-400'
            : 'bg-slate-100/80 dark:bg-slate-800/60 text-slate-400 dark:text-slate-500'
        }`}
      >
        {lang}
      </span>
    </div>
  )
}

function EmptyState({
  icon,
  message,
  iconBg,
  iconColor,
  textColor,
}: {
  icon: React.ReactNode
  message: string
  iconBg: string
  iconColor: string
  textColor: string
}) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center select-none">
        <div
          className={`w-11 h-11 rounded-2xl ${iconBg} flex items-center justify-center mx-auto mb-3 ${iconColor}`}
        >
          {icon}
        </div>
        <p className={`text-sm ${textColor} leading-relaxed`}>{message}</p>
      </div>
    </div>
  )
}
