'use client'

import { Meeting } from '@/types/meeting'

interface Props {
  meetings: Meeting[]
  activeMeetingId: string
  onSelect: (id: string) => void
  onClose: () => void
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

export default function MeetingSidebar({ meetings, activeMeetingId, onSelect, onClose }: Props) {
  return (
    <div className="flex flex-col h-full w-56 glass-sidebar border-r border-slate-200/60 dark:border-indigo-500/10">
      {/* Header */}
      <div className="px-4 h-12 flex items-center justify-between flex-shrink-0 border-b border-slate-100/70 dark:border-white/5">
        <span className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
          회의 목록
        </span>
        <button
          onClick={onClose}
          className="lg:hidden w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/8 transition-colors"
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
      </div>

      {/* Meeting list */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {meetings.map((meeting) => {
          const isActive = meeting.id === activeMeetingId
          return (
            <button
              key={meeting.id}
              onClick={() => {
                onSelect(meeting.id)
                onClose()
              }}
              className={`
                w-full text-left px-4 py-3 border-b border-slate-100/50 dark:border-white/4
                transition-colors
                ${
                  isActive
                    ? 'bg-indigo-50/90 dark:bg-indigo-500/12 border-l-2 border-l-indigo-500'
                    : 'hover:bg-slate-50/70 dark:hover:bg-white/4'
                }
              `}
            >
              <div
                className={`text-sm font-medium truncate ${
                  isActive
                    ? 'text-indigo-600 dark:text-indigo-400'
                    : 'text-slate-700 dark:text-slate-200'
                }`}
              >
                {meeting.title}
              </div>
              <div className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">
                {formatDate(meeting.startedAt)} · {meeting.messages.length}개
              </div>
            </button>
          )
        })}
      </div>

      {/* New meeting button */}
      <div className="p-3 flex-shrink-0 border-t border-slate-100/70 dark:border-white/5">
        <button className="w-full py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-600 text-white text-xs font-semibold hover:opacity-90 active:scale-95 transition-all shadow-sm shadow-indigo-500/20">
          + 새 회의
        </button>
      </div>
    </div>
  )
}
