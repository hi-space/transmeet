'use client'

interface Props {
  activeTab: 'voice' | 'notes'
  onTabChange: (tab: 'voice' | 'notes') => void
  hasNewVoice?: boolean
  hasNewNotes?: boolean
}

export default function MobileTabBar({ activeTab, onTabChange, hasNewVoice, hasNewNotes }: Props) {
  return (
    <div className="lg:hidden flex items-center flex-shrink-0 border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
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
                ? 'text-cyan-600 dark:text-cyan-500'
                : 'text-slate-500 dark:text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-500'
            }`}
          >
            {label}
            {hasBadge && !isActive && (
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 flex-shrink-0" />
            )}
            {isActive && (
              <span className="absolute bottom-0 left-1/4 right-1/4 h-[2px] rounded-full bg-cyan-500" />
            )}
          </button>
        )
      })}
    </div>
  )
}
