'use client'

import { useState, useRef, useEffect, useMemo, useId, type CSSProperties } from 'react'
import { Meeting } from '@/types/meeting'
import Icon from '@/components/ui/Icon'
import { usePanelResize } from '@/hooks/usePanelResize'

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
  isLoading?: boolean
  loadError?: string | null
  headingId?: string
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
  isLoading = false,
  loadError,
  headingId = 'meeting-list-title',
}: Props) {
  const instanceId = useId()
  const { width, minWidth, maxWidth, handlePointerDown, handleKeyDown } = usePanelResize({
    storageKey: 'transmeet-sidebar-width',
    defaultWidth: 240,
    minWidth: 200,
    maxWidth: 420,
    edge: 'right',
  })
  const [query, setQuery] = useState('')
  const [menuId, setMenuId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(new Set())
  const [collapsedPref, setCollapsedPref] = useState(false)
  const [isDesktop, setIsDesktop] = useState(false)
  const editInputRef = useRef<HTMLInputElement>(null)
  const filtered = useMemo(
    () =>
      meetings.filter((meeting) =>
        meeting.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
      ),
    [meetings, query]
  )
  const groups = useMemo(() => buildGroups(filtered), [filtered])
  const isRail = collapsedPref && isDesktop

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const sync = () => setIsDesktop(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    try {
      setCollapsedPref(window.localStorage.getItem(COLLAPSE_KEY) === '1')
    } catch {
      /* Storage is optional. */
    }
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    if (editingId) {
      editInputRef.current?.focus()
      editInputRef.current?.select()
    }
  }, [editingId])

  useEffect(() => {
    if (!menuId) return
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Element && !event.target.closest('[data-meeting-actions]'))
        setMenuId(null)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setMenuId(null)
      document.getElementById(`${instanceId}-meeting-menu-${menuId}`)?.focus()
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuId, instanceId])

  function toggleRail() {
    setCollapsedPref((prev) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, prev ? '0' : '1')
      } catch {
        /* Storage is optional. */
      }
      return !prev
    })
  }

  function toggleGroup(key: string) {
    setCollapsedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (isRail) {
    return (
      <div className="flex h-full w-16 flex-col items-center gap-3 border-r border-subtle bg-subtle py-4">
        <button
          type="button"
          onClick={toggleRail}
          className="ui-icon-button"
          aria-label="회의 목록 펼치기"
          title="회의 목록 펼치기"
        >
          <Icon name="panelLeft" className="h-[18px] w-[18px]" />
        </button>
        <button
          type="button"
          onClick={onNewMeeting}
          disabled={isCreating}
          className="ui-icon-button ui-button-primary"
          aria-label="새 회의"
          title="새 회의"
        >
          <Icon
            name={isCreating ? 'loader' : 'plus'}
            className={`h-[18px] w-[18px] ${isCreating ? 'animate-spin' : ''}`}
          />
        </button>
        <span className="mt-1 text-[0.6875rem] tabular-nums text-muted">{meetings.length}</span>
      </div>
    )
  }

  return (
    <div
      className="relative flex h-full w-[17rem] max-w-full flex-col border-r border-subtle bg-subtle lg:w-[var(--sidebar-width)]"
      data-sidebar-width={width}
      style={{ '--sidebar-width': `${width}px` } as CSSProperties}
    >
      <div
        role="separator"
        aria-label="회의 목록 너비 조절"
        aria-orientation="vertical"
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        tabIndex={0}
        title="드래그하여 회의 목록 너비 조절"
        onPointerDown={handlePointerDown}
        onKeyDown={handleKeyDown}
        className="absolute -right-1 bottom-0 top-0 z-20 hidden w-2 cursor-col-resize touch-none transition-colors hover:bg-accent-300/50 focus-visible:bg-accent-300/50 lg:block"
      />
      <div className="flex h-16 shrink-0 items-center justify-between gap-2 px-4">
        <div className="flex items-center gap-2">
          <h2 id={headingId} className="text-xs font-semibold text-ink">
            회의 목록
          </h2>
          <span className="text-[0.6875rem] tabular-nums text-muted">{meetings.length}</span>
        </div>
        <button
          type="button"
          onClick={toggleRail}
          className="ui-icon-button hidden lg:inline-flex"
          aria-label="회의 목록 접기"
          title="회의 목록 접기"
        >
          <Icon name="panelLeft" />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="ui-icon-button lg:hidden"
          aria-label="회의 목록 닫기"
        >
          <Icon name="close" />
        </button>
      </div>

      <div className="shrink-0 space-y-3 px-3 pb-3">
        <button
          type="button"
          onClick={() => {
            setQuery('')
            onNewMeeting()
            onClose()
          }}
          disabled={isCreating}
          className="ui-button ui-button-secondary w-full justify-start gap-2.5 px-3 text-xs"
        >
          <Icon
            name={isCreating ? 'loader' : 'plus'}
            className={`h-4 w-4 ${isCreating ? 'animate-spin' : ''}`}
          />
          {isCreating ? '회의 만드는 중…' : '새 회의'}
        </button>
        <div className="relative">
          <Icon
            name="search"
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted"
          />
          <input
            aria-label="회의 검색"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="회의 검색"
            className="ui-input min-h-9 bg-transparent py-2 pl-9 pr-8 text-xs"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="검색 지우기"
              className="ui-icon-button absolute right-0 top-1/2 -translate-y-1/2"
            >
              <Icon name="close" className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <nav
        aria-label="회의 기록"
        aria-busy={isLoading}
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 pb-4"
      >
        {isLoading && (
          <div role="status" className="flex items-center gap-2 px-3 py-4 text-xs text-muted">
            <Icon name="loader" className="h-3.5 w-3.5 animate-spin" />
            회의 목록 불러오는 중…
          </div>
        )}
        {loadError && (
          <p role="alert" className="px-3 py-4 text-xs text-red-600 dark:text-red-400">
            {loadError}
          </p>
        )}
        {groups.length === 0
          ? !isLoading &&
            !loadError && (
              <div className="ui-empty min-h-32 py-8 text-xs" role="status">
                <Icon name={query ? 'search' : 'calendar'} className="mb-1 h-6 w-6 opacity-60" />
                {query ? '검색 결과가 없습니다' : '아직 회의가 없습니다'}
                {query && (
                  <button type="button" onClick={() => setQuery('')} className="ui-button">
                    전체 회의 보기
                  </button>
                )}
              </div>
            )
          : groups.map((group) => {
              const collapsed = !query && collapsedKeys.has(group.key)
              return (
                <div key={group.key} className="mb-3">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                    aria-expanded={!collapsed}
                    className="flex min-h-9 w-full items-center gap-1.5 px-2 text-left text-[0.6875rem] font-medium text-muted"
                  >
                    <Icon
                      name="chevronDown"
                      className={`h-3 w-3 transition-transform ${collapsed ? '-rotate-90' : ''}`}
                    />
                    {group.label}
                    <span className="ml-auto tabular-nums">{group.meetings.length}</span>
                  </button>
                  {!collapsed &&
                    group.meetings.map((meeting) => {
                      const active = meeting.id === activeMeetingId
                      const count = Math.max(meeting.messageCount ?? 0, meeting.messages.length)
                      const date = formatDate(meeting.startedAt)
                      const time = formatTime(meeting.startedAt)
                      return (
                        <div
                          key={meeting.id}
                          className={`group relative mb-1 rounded-lg border transition-colors ${active ? 'border-accent-200/70 bg-accent-50 dark:border-accent-500/25 dark:bg-accent-500/10' : 'border-transparent hover:bg-slate-100 dark:hover:bg-slate-800/60'}`}
                        >
                          {editingId === meeting.id ? (
                            <form
                              className="space-y-2 p-2"
                              onSubmit={(event) => {
                                event.preventDefault()
                                const title = editTitle.trim()
                                if (title && title !== meeting.title)
                                  onUpdateTitle(meeting.id, title)
                                setEditingId(null)
                              }}
                            >
                              <input
                                ref={editInputRef}
                                aria-label="회의 제목"
                                value={editTitle}
                                onChange={(event) => setEditTitle(event.target.value)}
                                onKeyDown={(event) => {
                                  if (
                                    event.key === 'Enter' &&
                                    (event.nativeEvent.isComposing || event.keyCode === 229)
                                  )
                                    event.preventDefault()
                                  if (event.key === 'Escape') {
                                    event.preventDefault()
                                    setEditingId(null)
                                  }
                                }}
                                className="ui-input py-2 text-xs"
                              />
                              <div className="flex justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => setEditingId(null)}
                                  className="ui-button"
                                >
                                  취소
                                </button>
                                <button
                                  type="submit"
                                  className="ui-button ui-button-primary"
                                  disabled={!editTitle.trim()}
                                >
                                  저장
                                </button>
                              </div>
                            </form>
                          ) : confirmId === meeting.id ? (
                            <div className="p-3" role="group" aria-label="회의 삭제 확인">
                              <p className="text-xs font-medium text-ink">이 회의를 삭제할까요?</p>
                              <p className="mt-1 truncate text-xs text-muted">{meeting.title}</p>
                              <div className="mt-2 flex justify-end gap-1">
                                <button
                                  type="button"
                                  onClick={() => setConfirmId(null)}
                                  className="ui-button"
                                >
                                  취소
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setConfirmId(null)
                                    onDelete(meeting.id)
                                  }}
                                  className="ui-button bg-red-600 text-white hover:!bg-red-700 hover:!text-white"
                                >
                                  삭제
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  onSelect(meeting.id)
                                  onClose()
                                }}
                                aria-current={active ? 'page' : undefined}
                                className="w-full rounded-lg px-3 py-3 text-left"
                              >
                                <div className="flex min-w-0 items-center gap-2 pr-7">
                                  <span
                                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'bg-accent-600 dark:bg-accent-400' : 'bg-slate-300 dark:bg-slate-600'}`}
                                  />
                                  <span
                                    className={`truncate text-[0.8125rem] font-medium ${active ? 'text-accent-900 dark:text-accent-100' : 'text-ink'}`}
                                    title={meeting.title}
                                  >
                                    {meeting.title}
                                  </span>
                                </div>
                                <div className="mt-1.5 flex items-center gap-1.5 pl-3.5 text-[0.6875rem] text-muted">
                                  <span>{date ?? '날짜 없음'}</span>
                                  {time && (
                                    <>
                                      <span aria-hidden="true">·</span>
                                      <span className="tabular-nums">{time}</span>
                                    </>
                                  )}
                                  <span
                                    className="ml-auto flex items-center gap-1 tabular-nums"
                                    aria-label={`대화 ${count}개`}
                                  >
                                    <Icon name="message" className="h-3 w-3" />
                                    {count}
                                  </span>
                                </div>
                              </button>
                              <div className="absolute right-1 top-1.5" data-meeting-actions>
                                <button
                                  id={`${instanceId}-meeting-menu-${meeting.id}`}
                                  type="button"
                                  onClick={() =>
                                    setMenuId(menuId === meeting.id ? null : meeting.id)
                                  }
                                  aria-label={`${meeting.title} 작업`}
                                  aria-expanded={menuId === meeting.id}
                                  className="ui-icon-button h-8 w-8 min-h-8"
                                  title="회의 작업"
                                >
                                  <Icon name="more" className="h-4 w-4" />
                                </button>
                                {menuId === meeting.id && (
                                  <div
                                    className="ui-surface absolute right-0 top-full z-20 mt-1 flex w-40 flex-col p-1 shadow-lg"
                                    role="group"
                                    aria-label="회의 작업"
                                  >
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setMenuId(null)
                                        setEditingId(meeting.id)
                                        setEditTitle(meeting.title)
                                      }}
                                      className="ui-button justify-start"
                                    >
                                      <Icon name="notes" />
                                      제목 편집
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setMenuId(null)
                                        onGenerateTitle(meeting.id)
                                      }}
                                      disabled={generatingTitleId === meeting.id}
                                      className="ui-button justify-start"
                                    >
                                      <Icon
                                        name={
                                          generatingTitleId === meeting.id ? 'loader' : 'sparkles'
                                        }
                                      />
                                      제목 자동 생성
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setMenuId(null)
                                        setConfirmId(meeting.id)
                                      }}
                                      className="ui-button justify-start text-red-600 dark:text-red-400"
                                    >
                                      <Icon name="trash" />
                                      회의 삭제
                                    </button>
                                  </div>
                                )}
                              </div>
                            </>
                          )}
                        </div>
                      )
                    })}
                </div>
              )
            })}
      </nav>
    </div>
  )
}
