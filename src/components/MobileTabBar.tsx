'use client'

interface Props {
  activeTab: 'voice' | 'notes'
  onTabChange: (tab: 'voice' | 'notes') => void
  hasNewVoice?: boolean
  hasNewNotes?: boolean
}

export default function MobileTabBar({ activeTab, onTabChange, hasNewVoice, hasNewNotes }: Props) {
  return (
    <div className="lg:hidden flex items-center flex-shrink-0 border-b border-slate-200/60 dark:border-indigo-500/10 bg-white/50 dark:bg-slate-900/40 backdrop-blur-sm">
      {(['voice', 'notes'] as const).map((tab) => {
        const isActive = activeTab === tab
        const label = tab === 'voice' ? '음성 입력' : '내 메모'
        const hasBadge = tab === 'voice' ? hasNewVoice : hasNewNotes
        return (
          <button
            key={tab}
            onClick={() => onTabChange(tab)}
            className={`flex-1 relative flex items-center justify-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors ${
              isActive
                ? 'text-indigo-600 dark:text-indigo-400'
                : 'text-slate-500 dark:text-slate-400 hover:text-indigo-500 dark:hover:text-indigo-400'
            }`}
          >
            {label}
            {hasBadge && !isActive && (
              <span className="w-1.5 h-1.5 rounded-full bg-rose-500 flex-shrink-0" />
            )}
            {isActive && (
              <span className="absolute bottom-0 left-1/4 right-1/4 h-[2px] rounded-full bg-indigo-500 dark:bg-indigo-400" />
            )}
          </button>
        )
      })}
    </div>
  )
}
