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

const COLLAPSE_KEY = 'transmeet.sidebarCollapsed'

// startedAt 이 비어 있거나 파싱 불가한 회의도 들어올 수 있다.
// NaN 을 반환하는 비교 함수는 정렬 결과 전체를 뒤섞으므로 항상 유효한 값을 돌려준다.
function startedAtMs(iso: string | undefined): number {
  if (!iso) return NaN
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? NaN : ms
}

function formatDate(iso: string) {
  if (Number.isNaN(startedAtMs(iso))) return null
  return new Date(iso).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' })
}

function formatTime(iso: string) {
  if (Number.isNaN(startedAtMs(iso))) return null
  return new Date(iso).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

interface MeetingGroup {
  key: string
  label: string
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

  function push(key: string, label: string, meeting: Meeting) {
    const existing = byKey.get(key)
    if (existing) {
      existing.meetings.push(meeting)
      return
    }
    const group = { key, label, meetings: [meeting] }
    byKey.set(key, group)
    groups.push(group)
  }

  for (const meeting of sorted) {
    const ms = startedAtMs(meeting.startedAt)
    if (Number.isNaN(ms)) {
      push('unknown', '날짜 없음', meeting)
      continue
    }

    const date = new Date(ms)
    const day = startOfDay(date)

    if (day >= today) {
      push('today', '오늘', meeting)
    } else if (day === yesterday) {
      push('yesterday', '어제', meeting)
    } else if (day >= weekStart) {
      push('this-week', '이번 주', meeting)
    } else if (day >= lastWeekStart) {
      push('last-week', '지난 주', meeting)
    } else {
      push(
        `month-${date.getFullYear()}-${date.getMonth()}`,
        `${date.getFullYear()}년 ${date.getMonth() + 1}월`,
        meeting
      )
    }
  }

  return groups
}

function CalendarIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  )
}

function ChatIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z" />
    </svg>
  )
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
  const [collapsedPref, setCollapsedPref] = useState(false)
  // 데스크톱에서만 레일로 접는다. 모바일은 드로어이므로 항상 펼친 상태로 보여준다.
  const [isDesktop, setIsDesktop] = useState(false)
  const editInputRef = useRef<HTMLInputElement>(null)

  const groups = useMemo(() => buildGroups(meetings), [meetings])
  const isRail = collapsedPref && isDesktop

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const sync = () => setIsDesktop(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    setCollapsedPref(window.localStorage.getItem(COLLAPSE_KEY) === '1')
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    if (editingId && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingId])

  function toggleRail() {
    setCollapsedPref((prev) => {
      const next = !prev
      window.localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
      return next
    })
  }

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

  // ---- 접힌 레일 ----
  if (isRail) {
    return (
      <div className="flex flex-col h-full w-14 glass-sidebar border-r border-slate-200/60 dark:border-cyan-500/10">
        <div className="h-12 flex items-center justify-center flex-shrink-0 border-b border-slate-100 dark:border-slate-800">
          <button
            onClick={toggleRail}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-400 hover:bg-slate-100 dark:hover:bg-slate-800/60 transition-colors"
            aria-label="회의 목록 펼치기"
            title="회의 목록 펼치기"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-4 h-4"
            >
              <path d="M4 6h16M4 12h16M4 18h10" />
            </svg>
          </button>
        </div>

        <div className="pt-2 flex-shrink-0 flex justify-center">
          <button
            onClick={onNewMeeting}
            disabled={isCreating}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-cyan-600 dark:text-cyan-400 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            aria-label="새 회의"
            title="새 회의"
          >
            {isCreating ? (
              <svg
                className="w-4 h-4 animate-spin"
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
                className="w-4 h-4"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
            )}
          </button>
        </div>

        {/* 접힌 상태에서는 목록을 노출하지 않는다. 세로 라벨만 두어 무엇을 접었는지 알린다. */}
        <button
          onClick={toggleRail}
          className="flex-1 w-full flex items-center justify-center group"
          aria-label="회의 목록 펼치기"
          title="회의 목록 펼치기"
        >
          <span
            className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600 group-hover:text-cyan-600 dark:group-hover:text-cyan-400 transition-colors select-none"
            style={{ writingMode: 'vertical-rl' }}
          >
            회의 목록
          </span>
        </button>
      </div>
    )
  }

  // ---- 펼친 목록 ----
  return (
    <div className="flex flex-col h-full w-[19rem] glass-sidebar border-r border-slate-200/60 dark:border-cyan-500/10">
      {/* Header */}
      <div className="px-3 h-12 flex items-center justify-between flex-shrink-0 border-b border-slate-100 dark:border-slate-800">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-bold tracking-tight text-slate-700 dark:text-slate-200">
            회의 목록
          </span>
          <span className="px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-[10px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums">
            {meetings.length}
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          {/* 새 회의 — 목록 하단이 아니라 헤더에 둔다 (스크롤과 무관하게 항상 보임) */}
          <button
            onClick={onNewMeeting}
            disabled={isCreating}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-cyan-600 dark:text-cyan-400 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            aria-label="새 회의"
            title="새 회의"
          >
            {isCreating ? (
              <svg
                className="w-4 h-4 animate-spin"
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
                className="w-4 h-4"
              >
                <path d="M12 5v14M5 12h14" />
              </svg>
            )}
          </button>
          {/* 데스크톱: 레일로 접기 */}
          <button
            onClick={toggleRail}
            className="hidden lg:flex w-7 h-7 items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors"
            aria-label="회의 목록 접기"
            title="회의 목록 접기"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-4 h-4"
            >
              <path d="m11 17-5-5 5-5M18 17l-5-5 5-5" />
            </svg>
          </button>
          {/* 모바일: 드로어 닫기 */}
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
      </div>

      {/* Meeting list */}
      <div className="flex-1 overflow-y-auto scrollbar-thin pb-2">
        {meetings.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 px-6 text-center select-none">
            <CalendarIcon className="w-6 h-6 text-slate-300 dark:text-slate-700" />
            <span className="text-xs text-slate-400 dark:text-slate-500">아직 회의가 없습니다</span>
          </div>
        ) : (
          groups.map((groupItem) => {
            const isCollapsed = collapsedKeys.has(groupItem.key)
            const hasActive = groupItem.meetings.some((m) => m.id === activeMeetingId)
            return (
              <div key={groupItem.key} className="mb-0.5">
                {/* Group header */}
                <button
                  onClick={() => toggleGroup(groupItem.key)}
                  className="sticky top-0 z-10 w-full flex items-center gap-1.5 px-3 py-2 bg-slate-100/90 dark:bg-slate-900/95 backdrop-blur-md border-y border-slate-200/70 dark:border-slate-800 text-left hover:bg-slate-200/80 dark:hover:bg-slate-800/95 transition-colors"
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className={`w-3 h-3 flex-shrink-0 text-slate-400 dark:text-slate-500 transition-transform ${
                      isCollapsed ? '-rotate-90' : ''
                    }`}
                  >
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-600 dark:text-slate-300">
                    {groupItem.label}
                  </span>
                  <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">
                    {groupItem.meetings.length}
                  </span>
                  {isCollapsed && hasActive && (
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-500" aria-hidden />
                  )}
                </button>

                {!isCollapsed && (
                  <div>
                    {groupItem.meetings.map((meeting) => {
                      const isActive = meeting.id === activeMeetingId
                      const isConfirming = confirmId === meeting.id
                      const isEditing = editingId === meeting.id
                      const date = formatDate(meeting.startedAt)
                      const time = formatTime(meeting.startedAt)
                      const count = meeting.messageCount ?? meeting.messages.length
                      return (
                        <div
                          key={meeting.id}
                          className={`group relative border-b border-slate-100 dark:border-slate-800/70 transition-colors ${
                            isActive
                              ? 'bg-cyan-50/70 dark:bg-cyan-500/10'
                              : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'
                          }`}
                        >
                          <button
                            onClick={() => {
                              if (isConfirming || isEditing) return
                              onSelect(meeting.id)
                              onClose()
                            }}
                            className="w-full text-left px-4 py-4"
                          >
                            {/* 1행: 제목 */}
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
                                className="w-full text-sm font-semibold bg-white dark:bg-slate-900 border border-cyan-300 dark:border-cyan-500/40 rounded-md px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-cyan-400 text-slate-800 dark:text-slate-100"
                              />
                            ) : (
                              <div
                                onDoubleClick={(e) => {
                                  e.stopPropagation()
                                  startEditing(meeting)
                                }}
                                className={`text-sm font-semibold leading-snug truncate pr-16 ${
                                  isActive
                                    ? 'text-cyan-900 dark:text-cyan-100'
                                    : 'text-slate-800 dark:text-slate-100'
                                }`}
                                title={meeting.title}
                              >
                                {meeting.title}
                              </div>
                            )}

                            {/* 2행: 날짜 / 시간 / 메시지 수 — 얇은 구분선으로 항목을 나눈다 */}
                            <div className="mt-1.5 flex items-center gap-2 text-xs tabular-nums">
                              <span
                                className={`font-medium ${
                                  isActive
                                    ? 'text-cyan-700 dark:text-cyan-300'
                                    : 'text-slate-500 dark:text-slate-400'
                                }`}
                              >
                                {date ?? '날짜 없음'}
                              </span>
                              {time && (
                                <>
                                  <span
                                    className="w-px h-2.5 bg-slate-300 dark:bg-slate-700"
                                    aria-hidden
                                  />
                                  <span
                                    className={`font-semibold ${
                                      isActive
                                        ? 'text-cyan-700 dark:text-cyan-300'
                                        : 'text-slate-600 dark:text-slate-300'
                                    }`}
                                  >
                                    {time}
                                  </span>
                                </>
                              )}
                              <span className="ml-auto inline-flex items-center gap-1 text-slate-400 dark:text-slate-500">
                                <ChatIcon className="w-3 h-3" />
                                {count}
                              </span>
                            </div>
                          </button>

                          {/* Action buttons (hover) */}
                          {!isConfirming && !isEditing && (
                            <div className="absolute right-1.5 top-3 opacity-0 group-hover:opacity-100 focus-within:opacity-100 flex items-center gap-0.5 transition-opacity">
                              {/* Edit title button */}
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  startEditing(meeting)
                                }}
                                className="w-6 h-6 flex items-center justify-center rounded-md bg-white/80 dark:bg-slate-900/80 text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-400 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 transition-colors"
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
                                className="w-6 h-6 flex items-center justify-center rounded-md bg-white/80 dark:bg-slate-900/80 text-slate-400 hover:text-cyan-600 dark:hover:text-cyan-400 hover:bg-cyan-50 dark:hover:bg-cyan-500/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
                                className="w-6 h-6 flex items-center justify-center rounded-md bg-white/80 dark:bg-slate-900/80 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors"
                                aria-label="삭제"
                                title="삭제"
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
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
