'use client'

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import Icon from '@/components/ui/Icon'

interface Props {
  isRecording: boolean
  onToggleRecording: () => void
  recordingDisabled?: boolean
  recordingLabel?: string
  audioLevel?: number
  onEndMeeting?: () => void
  isEndingMeeting?: boolean
  endMeetingDisabled?: boolean
  audioSource?: 'mic' | 'system' | 'both'
  meetingActions?: ReactNode
  status?: ReactNode
  avoidElementRef?: RefObject<HTMLElement>
}

export default function ControlPanel({
  isRecording,
  onToggleRecording,
  recordingDisabled = false,
  recordingLabel,
  audioLevel = 0,
  onEndMeeting,
  isEndingMeeting = false,
  endMeetingDisabled = false,
  audioSource = 'mic',
  meetingActions,
  status,
  avoidElementRef,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [bottomInset, setBottomInset] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const sourceLabel = { mic: '마이크', system: '시스템 오디오', both: '마이크 + 시스템' }[
    audioSource
  ]
  const hasActions = !!(onEndMeeting || meetingActions)
  const preparing = !isRecording && recordingLabel === '엔진 준비 중'

  // Keep floating controls above an editor or page toolbar, including font-size
  // and viewport changes. Desktop tools live in the other column.
  useEffect(() => {
    const element = avoidElementRef?.current
    if (!element) {
      setBottomInset(0)
      return
    }
    const update = () => setBottomInset(Math.ceil(element.getBoundingClientRect().height))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [avoidElementRef])

  const closeMenu = useCallback((restoreFocus: boolean) => {
    setExpanded(false)
    if (restoreFocus)
      requestAnimationFrame(() => menuButtonRef.current?.focus({ preventScroll: true }))
  }, [])

  useEffect(() => {
    if (!expanded) return
    const first = menuRef.current?.querySelector<HTMLButtonElement>('button:not([disabled])')
    if (first) first.focus({ preventScroll: true })
    else menuRef.current?.focus({ preventScroll: true })
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target))
        closeMenu(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      closeMenu(true)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [expanded, closeMenu])

  return (
    <div
      ref={containerRef}
      className="pointer-events-none absolute left-7 z-30 flex flex-col items-start gap-2 sm:left-9"
      style={{ bottom: `calc(max(2rem, env(safe-area-inset-bottom)) + ${bottomInset}px)` }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) closeMenu(false)
      }}
    >
      <div className={expanded ? 'hidden' : 'contents pointer-events-auto'}>{status}</div>
      {expanded && hasActions && (
        <div
          ref={menuRef}
          id={menuId}
          role="group"
          aria-label="녹음 작업"
          tabIndex={-1}
          className="ui-surface pointer-events-auto absolute bottom-full left-0 mb-2 w-52 overflow-y-auto p-1.5 shadow-xl"
          style={{ maxHeight: `calc(100dvh - ${bottomInset}px - 8rem)` }}
        >
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted">
            <Icon name="mic" className="h-3.5 w-3.5" />
            {sourceLabel}
          </div>
          {onEndMeeting && (
            <button
              type="button"
              onClick={() => {
                onEndMeeting()
                closeMenu(true)
              }}
              disabled={endMeetingDisabled || isEndingMeeting}
              aria-label="회의 종료"
              aria-busy={isEndingMeeting}
              className="ui-button min-h-11 w-full justify-start"
              title={
                endMeetingDisabled
                  ? '서버에 연결되면 회의를 종료할 수 있습니다'
                  : '녹음을 멈추고 회의 정리'
              }
            >
              <Icon
                name={isEndingMeeting ? 'loader' : 'stop'}
                className={`h-4 w-4 ${isEndingMeeting ? 'animate-spin' : ''}`}
              />
              {isEndingMeeting ? '종료 중…' : '회의 종료'}
            </button>
          )}
          <div onClick={() => closeMenu(true)}>{meetingActions}</div>
        </div>
      )}
      <div
        role="group"
        aria-label="녹음 조작"
        className="pointer-events-auto flex items-center gap-2"
      >
        <button
          type="button"
          onClick={() => {
            closeMenu(false)
            onToggleRecording()
          }}
          aria-label={
            isEndingMeeting
              ? '회의 종료 중'
              : isRecording
                ? '녹음 중지'
                : (recordingLabel ?? '녹음 시작')
          }
          aria-pressed={isRecording}
          title={`${isRecording ? '녹음 중지' : (recordingLabel ?? '녹음 시작')} · ${sourceLabel}`}
          disabled={isEndingMeeting || (recordingDisabled && !isRecording)}
          className={`relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full shadow-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:h-14 sm:w-14 ${isRecording ? 'bg-red-600 text-white hover:bg-red-700' : 'bg-primary text-primary-foreground hover:bg-primary-hover'}`}
          style={
            isRecording
              ? {
                  boxShadow: `0 0 0 ${3 + Math.min(1, audioLevel) * 4}px rgb(239 68 68 / 0.16), 0 4px 12px rgb(0 0 0 / 0.12)`,
                }
              : undefined
          }
        >
          <Icon
            name={isEndingMeeting || preparing ? 'loader' : isRecording ? 'stop' : 'mic'}
            className={`h-5 w-5 ${isEndingMeeting || preparing ? 'animate-spin' : ''}`}
          />
        </button>
        {hasActions && (
          <button
            ref={menuButtonRef}
            type="button"
            onClick={() => (expanded ? closeMenu(true) : setExpanded(true))}
            aria-label="녹음 작업 더보기"
            aria-expanded={expanded}
            aria-controls={expanded ? menuId : undefined}
            title="회의 종료 · 녹음 다운로드"
            className="ui-icon-button h-10 w-10 rounded-full border border-subtle bg-panel shadow-md"
          >
            <Icon name={expanded ? 'close' : 'more'} className="h-[18px] w-[18px]" />
          </button>
        )}
      </div>
    </div>
  )
}
