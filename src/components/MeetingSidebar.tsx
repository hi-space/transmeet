'use client'

import { useState } from 'react'
import { Meeting } from '@/types/meeting'

interface Props {
  meetings: Meeting[]
  activeMeetingId: string
  onSelect: (id: string) => void
  onClose: () => void
  onNewMeeting: () => void
  onDelete: (id: string) => void
  onGenerateTitle: (id: string) => void
  generatingTitleId: string | null
  isCreating?: boolean
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

export default function MeetingSidebar({
  meetings,
  activeMeetingId,
  onSelect,
  onClose,
  onNewMeeting,
  onDelete,
  onGenerateTitle,
  generatingTitleId,
  isCreating,
}: Props) {
  const [confirmId, setConfirmId] = useState<string | null>(null)

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
        {meetings.length === 0 ? (
          <div className="flex items-center justify-center h-full text-xs text-slate-400 dark:text-slate-500 select-none">
            회의 없음
          </div>
        ) : (
          meetings.map((meeting) => {
            const isActive = meeting.id === activeMeetingId
            const isConfirming = confirmId === meeting.id
            return (
              <div
                key={meeting.id}
                className={`
                  group relative border-b border-slate-100/50 dark:border-white/4
                  ${
                    isActive
                      ? 'bg-indigo-50/90 dark:bg-indigo-500/12 border-l-2 border-l-indigo-500'
                      : 'hover:bg-slate-50/70 dark:hover:bg-white/4'
                  }
                `}
              >
                <button
                  onClick={() => {
                    if (isConfirming) return
                    onSelect(meeting.id)
                    onClose()
                  }}
                  className="w-full text-left px-4 py-3 pr-16 transition-colors"
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

                {/* Action buttons (hover) */}
                {!isConfirming && (
                  <div className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-all">
                    {/* Generate title button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        onGenerateTitle(meeting.id)
                      }}
                      disabled={generatingTitleId === meeting.id}
                      className="w-6 h-6 flex items-center justify-center rounded-md text-slate-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-500/10 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                      aria-label="제목 생성"
                      title="AI로 제목 생성"
                    >
                      {generatingTitleId === meeting.id ? (
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
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="w-3 h-3"
                        >
                          <path d="M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 1 7.92 12.446A5 5 0 1 1 10 19H5a4 4 0 0 1-.608-7.95A6 6 0 0 1 12 3z" />
                          <path d="m10 13 2 2 4-4" />
                        </svg>
                      )}
                    </button>
                    {/* Delete button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmId(meeting.id)
                      }}
                      className="w-6 h-6 flex items-center justify-center rounded-md text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                      aria-label="삭제"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        className="w-3.5 h-3.5"
                      >
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        <path d="M10 11v6M14 11v6" />
                        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                      </svg>
                    </button>
                  </div>
                )}

                {/* Inline confirm */}
                {isConfirming && (
                  <div className="absolute inset-0 flex items-center justify-between px-3 bg-red-50/95 dark:bg-red-950/80 backdrop-blur-sm">
                    <span className="text-xs text-red-600 dark:text-red-400 font-medium">
                      삭제하시겠습니까?
                    </span>
                    <div className="flex gap-1">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmId(null)
                          onDelete(meeting.id)
                        }}
                        className="px-2 py-1 rounded text-xs font-semibold bg-red-500 text-white hover:bg-red-600 transition-colors"
                      >
                        삭제
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmId(null)
                        }}
                        className="px-2 py-1 rounded text-xs font-semibold text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-white/10 transition-colors"
                      >
                        취소
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>

      {/* New meeting button */}
      <div className="p-3 flex-shrink-0 border-t border-slate-100/70 dark:border-white/5">
        <button
          onClick={onNewMeeting}
          disabled={isCreating}
          className="w-full py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-600 text-white text-xs font-semibold hover:opacity-90 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm shadow-indigo-500/20 flex items-center justify-center gap-1.5"
        >
          {isCreating ? (
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
              <span>생성 중...</span>
            </>
          ) : (
            <span>+ 새 회의</span>
          )}
        </button>
      </div>
    </div>
  )
}
