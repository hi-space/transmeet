'use client'

import { useState, useRef, useEffect, useMemo } from 'react'
import { Meeting } from '@/types/meeting'

interface Props {
  meetings: Meeting[]
  activeMeetingId: string
  onSelect: (id: string) => void
  onClose: () => void
  onNewMeeting: () => void
  onDelete: (id: string) => void
  onUpdateTitle: (id: string, title: string) => void
  onGenerateTitle: (id: string) => void
  generatingTitleId: string | null
  isCreating?: boolean
}

// startedAt 이 비어 있거나 파싱 불가한 회의도 들어올 수 있다.
// NaN 을 반환하는 비교 함수는 정렬 결과 전체를 뒤섞으므로 항상 유효한 값을 돌려준다.
function startedAtMs(iso: string | undefined): number {
  if (!iso) return NaN
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? NaN : ms
}

function formatDate(iso: string) {
  if (Number.isNaN(startedAtMs(iso))) return '날짜 없음'
  return new Date(iso).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

function formatTime(iso: string) {
  if (Number.isNaN(startedAtMs(iso))) return '날짜 없음'
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** 오늘/어제 그룹은 날짜가 헤더에 드러나므로 시각만, 그 외는 날짜와 시각을 함께 표시 */
function formatDateTime(iso: string, timeOnly: boolean) {
  if (Number.isNaN(startedAtMs(iso))) return '날짜 없음'
  return timeOnly ? formatTime(iso) : `${formatDate(iso)} ${formatTime(iso)}`
}

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

interface MeetingGroup {
  key: string
  label: string
  /** 오늘/어제 그룹은 시각, 그 외는 날짜를 부가 정보로 표시 */
  showTime: boolean
  meetings: Meeting[]
}

/** 최근 회의는 오늘/어제/이번 주/지난 주로, 그보다 오래된 회의는 월별로 묶는다 */
function buildGroups(meetings: Meeting[]): MeetingGroup[] {
  const today = startOfDay(new Date())
  const dayMs = 86400000
  const yesterday = today - dayMs
  // 주 시작은 월요일 기준. 월요일에는 이번 주 구간이 비므로 지난 주까지 함께 둔다
  const weekStart = today - ((new Date(today).getDay() + 6) % 7) * dayMs
  const lastWeekStart = weekStart - 7 * dayMs

  const groups: MeetingGroup[] = []
  const byKey = new Map<string, MeetingGroup>()

  // 최신순. 날짜 없는 회의는 맨 뒤로 밀되 id 로 순서를 고정한다.
  const sorted = [...meetings].sort((a, b) => {
    const am = startedAtMs(a.startedAt)
    const bm = startedAtMs(b.startedAt)
    const aInvalid = Number.isNaN(am)
    const bInvalid = Number.isNaN(bm)
    if (aInvalid || bInvalid) {
      if (aInvalid && bInvalid) return a.id.localeCompare(b.id)
      return aInvalid ? 1 : -1
    }
    if (am !== bm) return bm - am
    return a.id.localeCompare(b.id)
  })

  for (const meeting of sorted) {
    const ms = startedAtMs(meeting.startedAt)
    if (Number.isNaN(ms)) {
      const key = 'unknown'
      const existing = byKey.get(key)
      if (existing) {
        existing.meetings.push(meeting)
      } else {
        const group = { key, label: '날짜 없음', showTime: false, meetings: [meeting] }
        byKey.set(key, group)
        groups.push(group)
      }
      continue
    }

    const date = new Date(ms)
    const day = startOfDay(date)

    let key: string
    let label: string
    let showTime = false

    if (day >= today) {
      key = 'today'
      label = '오늘'
      showTime = true
    } else if (day === yesterday) {
      key = 'yesterday'
      label = '어제'
      showTime = true
    } else if (day >= weekStart) {
      key = 'this-week'
      label = '이번 주'
    } else if (day >= lastWeekStart) {
      key = 'last-week'
      label = '지난 주'
    } else {
      key = `month-${date.getFullYear()}-${date.getMonth()}`
      label = `${date.getFullYear()}년 ${date.getMonth() + 1}월`
    }

    const existing = byKey.get(key)
    if (existing) {
      existing.meetings.push(meeting)
    } else {
      const group = { key, label, showTime, meetings: [meeting] }
      byKey.set(key, group)
      groups.push(group)
    }
  }

  return groups
}

export default function MeetingSidebar({
  meetings,
  activeMeetingId,
  onSelect,
  onClose,
  onNewMeeting,
  onDelete,
  onUpdateTitle,
  onGenerateTitle,
  generatingTitleId,
  isCreating,
}: Props) {
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(new Set())
  const editInputRef = useRef<HTMLInputElement>(null)

  const groups = useMemo(() => buildGroups(meetings), [meetings])

  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingId])

  function startEditing(meeting: Meeting) {
    setConfirmId(null)
    setEditingId(meeting.id)
    setEditTitle(meeting.title)
  }

  function commitEdit(meeting: Meeting) {
    const trimmed = editTitle.trim()
    setEditingId(null)
    if (trimmed && trimmed !== meeting.title) {
      onUpdateTitle(meeting.id, trimmed)
    }
  }

  function cancelEdit() {
    setEditingId(null)
  }

  function toggleGroup(key: string) {
    setCollapsedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full w-72 glass-sidebar border-r border-slate-200/60 dark:border-cyan-500/10">
      {/* Header */}
      <div className="px-4 h-12 flex items-center justify-between flex-shrink-0 border-b border-slate-100 dark:border-slate-800">
        <span className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
          회의 목록
        </span>
        <button
          onClick={onClose}
          className="lg:hidden w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors"
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
          groups.map((groupItem) => {
            const isCollapsed = collapsedKeys.has(groupItem.key)
            const hasActive = groupItem.meetings.some((m) => m.id === activeMeetingId)
            return (
              <div key={groupItem.key}>
                {/* Group header */}
                <button
                  onClick={() => toggleGroup(groupItem.key)}
                  className="sticky top-0 z-10 w-full flex items-center gap-1.5 px-3 py-1.5 bg-slate-50/95 dark:bg-slate-900/95 backdrop-blur-md border-b border-slate-200/70 dark:border-slate-800 text-left hover:bg-slate-100/95 dark:hover:bg-slate-800/95 transition-colors"
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className={`w-3 h-3 text-slate-400 dark:text-slate-500 transition-transform ${
                      isCollapsed ? '-rotate-90' : ''
                    }`}
                  >
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                    {groupItem.label}
                  </span>
                  <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">
                    {groupItem.meetings.length}
                  </span>
                  {isCollapsed && hasActive && (
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-500" aria-hidden />
                  )}
                </button>

                {!isCollapsed &&
                  groupItem.meetings.map((meeting) => {
                    const isActive = meeting.id === activeMeetingId
                    const isConfirming = confirmId === meeting.id
                    const isEditing = editingId === meeting.id
                    return (
                      <div
                        key={meeting.id}
                        className={`
                  group relative border-b border-slate-100 dark:border-slate-800
                  ${
                    isActive
                      ? 'bg-slate-100 dark:bg-slate-800 border-l-2 border-l-cyan-500'
                      : 'hover:bg-slate-50 dark:hover:bg-slate-800/50'
                  }
                `}
                      >
                        <button
                          onClick={() => {
                            if (isConfirming || isEditing) return
                            onSelect(meeting.id)
                            onClose()
                          }}
                          className="w-full text-left px-4 py-3 pr-16 transition-colors"
                        >
                          {isEditing ? (
                            <input
                              ref={editInputRef}
                              value={editTitle}
                              onChange={(e) => setEditTitle(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault()
                                  commitEdit(meeting)
                                } else if (e.key === 'Escape') {
                                  cancelEdit()
                                }
                              }}
                              onBlur={() => commitEdit(meeting)}
                              onClick={(e) => e.stopPropagation()}
                              className="w-full text-sm font-medium bg-white dark:bg-slate-800 border border-cyan-300 dark:border-cyan-500/40 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-cyan-400 text-slate-700 dark:text-slate-200"
                            />
                          ) : (
                            <div
                              onDoubleClick={(e) => {
                                e.stopPropagation()
                                startEditing(meeting)
                              }}
                              className={`text-sm font-medium truncate ${
                                isActive
                                  ? 'text-slate-900 dark:text-slate-100'
                                  : 'text-slate-700 dark:text-slate-200'
                              }`}
                            >
                              {meeting.title}
                            </div>
                          )}
                          <div className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">
                            {formatDateTime(meeting.startedAt, groupItem.showTime)}
                            {' · '}
                            {meeting.messageCount ?? meeting.messages.length}개
                          </div>
                        </button>

                        {/* Action buttons (hover) */}
                        {!isConfirming && !isEditing && (
                          <div className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-all">
                            {/* Edit title button */}
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                startEditing(meeting)
                              }}
                              className="w-6 h-6 flex items-center justify-center rounded-md text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-500 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 transition-all"
                              aria-label="제목 편집"
                              title="제목 편집"
                            >
                              <svg
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                className="w-3 h-3"
                              >
                                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                                <path d="m15 5 4 4" />
                              </svg>
                            </button>
                            {/* Generate title button */}
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                onGenerateTitle(meeting.id)
                              }}
                              disabled={generatingTitleId === meeting.id}
                              className="w-6 h-6 flex items-center justify-center rounded-md text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-500 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
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
                                setEditingId(null)
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
                          <div className="absolute inset-0 flex items-center justify-between px-3 bg-red-50 dark:bg-red-950">
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
                  })}
              </div>
            )
          })
        )}
      </div>

      {/* New meeting button */}
      <div className="p-3 flex-shrink-0 border-t border-slate-100 dark:border-slate-800">
        <button
          onClick={onNewMeeting}
          disabled={isCreating}
          className="w-full py-2 rounded-xl bg-cyan-600 hover:bg-cyan-700 text-white text-xs font-semibold active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm shadow-cyan-500/20 flex items-center justify-center gap-1.5"
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
