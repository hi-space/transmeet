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
import Icon, { type IconName } from '@/components/ui/Icon'

interface Props {
  isRecording: boolean
  isRecordingPaused?: boolean
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
  anchorElementRef?: RefObject<HTMLElement>
}

export default function ControlPanel({
  isRecording,
  isRecordingPaused = false,
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
  anchorElementRef,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [bottomInset, setBottomInset] = useState(0)
  const [anchorInset, setAnchorInset] = useState({ right: 0, bottom: 0, width: 0 })
  const containerRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const sourceLabel = { mic: '마이크', system: '시스템 오디오', both: '마이크 + 시스템' }[
    audioSource
  ]
  const showEndMeeting = !!onEndMeeting && (isRecording || isRecordingPaused || isEndingMeeting)
  const hasActions = !!(meetingActions || (onEndMeeting && !showEndMeeting))
  const preparing = !isRecording && recordingLabel === '엔진 준비 중'
  const recordingText = isRecording
    ? '일시정지'
    : (recordingLabel ?? (isRecordingPaused ? '녹음 재개' : '녹음 시작'))
  const recordingIcon: IconName = preparing
    ? 'loader'
    : isRecording
      ? 'pause'
      : isRecordingPaused && !recordingLabel
        ? 'play'
        : 'mic'
  const endMeetingTitle = endMeetingDisabled
    ? '서버 연결과 회의 준비가 완료되면 종료할 수 있습니다'
    : '녹음을 마무리하고 회의를 정리합니다'

  // The recording controls follow the conversation panel, not the viewport.
  useEffect(() => {
    const anchor = anchorElementRef?.current
    const parent = containerRef.current?.offsetParent
    if (!anchor || !(parent instanceof HTMLElement)) return
    const update = () => {
      const panel = anchor.getBoundingClientRect()
      if (!panel.width || !panel.height) return
      const bounds = parent.getBoundingClientRect()
      setAnchorInset({
        right: Math.max(0, bounds.right - panel.right),
        bottom: Math.max(0, bounds.bottom - panel.bottom),
        width: panel.width,
      })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(anchor)
    observer.observe(parent)
    return () => observer.disconnect()
  }, [anchorElementRef])

  // Wrapped controls need matching scroll clearance below the transcript.
  useEffect(() => {
    const anchor = anchorElementRef?.current
    const controls = containerRef.current
    if (!anchor || !controls) return
    const update = () =>
      anchor.style.setProperty(
        '--recording-controls-height',
        `${Math.ceil(controls.getBoundingClientRect().height)}px`
      )
    update()
    const observer = new ResizeObserver(update)
    observer.observe(controls)
    return () => {
      observer.disconnect()
      anchor.style.removeProperty('--recording-controls-height')
    }
  }, [anchorElementRef])

  // Keep floating controls above the visible editor or page toolbar, including
  // font-size, panel-width, and viewport changes.
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
      className="pointer-events-none absolute z-30 flex flex-col items-end gap-2"
      style={{
        right: `calc(max(1rem, env(safe-area-inset-right)) + ${anchorInset.right}px)`,
        bottom: `calc(max(1rem, env(safe-area-inset-bottom)) + ${anchorInset.bottom + bottomInset}px)`,
        maxWidth: anchorInset.width ? `calc(${anchorInset.width}px - 2rem)` : 'calc(100% - 2rem)',
      }}
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
          className="ui-surface pointer-events-auto absolute bottom-full right-0 mb-2 w-52 overflow-y-auto p-1.5 shadow-xl"
          style={{
            maxHeight: `calc(100dvh - ${bottomInset + anchorInset.bottom}px - 8rem)`,
          }}
        >
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted">
            <Icon name="mic" className="h-3.5 w-3.5" />
            {sourceLabel}
          </div>
          {onEndMeeting && !showEndMeeting && (
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
              title={endMeetingTitle}
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
        className="pointer-events-auto flex max-w-full flex-wrap items-center justify-end gap-2"
      >
        {hasActions && (
          <button
            ref={menuButtonRef}
            type="button"
            onClick={() => (expanded ? closeMenu(true) : setExpanded(true))}
            aria-label="녹음 작업 더보기"
            aria-expanded={expanded}
            aria-controls={expanded ? menuId : undefined}
            title={showEndMeeting ? '녹음 다운로드' : '회의 종료 · 녹음 다운로드'}
            className="recording-control ui-icon-button h-11 w-11 rounded-full border border-subtle bg-panel shadow-md"
          >
            <Icon name={expanded ? 'close' : 'more'} className="h-[18px] w-[18px]" />
          </button>
        )}
        {!isEndingMeeting && (
          <button
            type="button"
            onClick={() => {
              closeMenu(false)
              onToggleRecording()
            }}
            aria-label={isRecording ? '녹음 일시정지' : recordingText}
            aria-pressed={isRecording}
            title={`${
              isRecording ? '녹음만 잠시 멈춥니다. 회의는 계속됩니다' : recordingText
            } · ${sourceLabel}`}
            disabled={recordingDisabled && !isRecording}
            className={
              showEndMeeting || isRecording
                ? 'recording-control ui-button ui-button-secondary h-12 max-w-full gap-1.5 rounded-full px-3 shadow-md sm:h-14 sm:gap-2 sm:px-4 sm:text-sm'
                : 'recording-control flex h-12 max-w-full shrink-0 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-lg transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50 sm:h-14'
            }
          >
            <Icon
              name={recordingIcon}
              className={`h-4 w-4 sm:h-5 sm:w-5 ${preparing ? 'animate-spin' : ''}`}
              {...(recordingIcon === 'pause' || recordingIcon === 'play'
                ? { fill: 'currentColor', strokeWidth: 0 }
                : {})}
            />
            <span className="min-w-0">{recordingText}</span>
          </button>
        )}
        {showEndMeeting && (
          <button
            type="button"
            onClick={() => {
              closeMenu(false)
              onEndMeeting?.()
            }}
            aria-label={isEndingMeeting ? '회의 종료 중' : '회의 종료'}
            aria-busy={isEndingMeeting}
            title={endMeetingTitle}
            disabled={endMeetingDisabled || isEndingMeeting}
            className="recording-control relative flex h-12 max-w-full shrink-0 items-center justify-center gap-2 rounded-full bg-red-600 px-4 text-sm font-semibold text-white shadow-lg transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50 sm:h-14"
            style={
              isRecording
                ? {
                    boxShadow: `0 0 0 ${3 + Math.min(1, audioLevel) * 4}px rgb(239 68 68 / 0.16), 0 4px 12px rgb(0 0 0 / 0.12)`,
                  }
                : undefined
            }
          >
            <Icon
              name={isEndingMeeting ? 'loader' : 'stop'}
              className={`h-5 w-5 ${isEndingMeeting ? 'animate-spin' : ''}`}
              {...(!isEndingMeeting ? { fill: 'currentColor', strokeWidth: 0 } : {})}
            />
            <span className="min-w-0">{isEndingMeeting ? '종료 중…' : '회의 종료'}</span>
          </button>
        )}
      </div>
    </div>
  )
}
