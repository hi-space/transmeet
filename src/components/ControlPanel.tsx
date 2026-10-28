'use client'

import type { ReactNode } from 'react'
import Icon from '@/components/ui/Icon'

const WAVE_HEIGHTS = [7, 12, 18, 11, 15, 8, 13]

interface Props {
  isRecording: boolean
  onToggleRecording: () => void
  recordingDisabled?: boolean
  audioLevel?: number
  onEndMeeting?: () => void
  isEndingMeeting?: boolean
  endMeetingDisabled?: boolean
  audioSource?: 'mic' | 'system' | 'both'
  meetingActions?: ReactNode
  status?: ReactNode
}

export default function ControlPanel({
  isRecording,
  onToggleRecording,
  recordingDisabled = false,
  audioLevel = 0,
  onEndMeeting,
  isEndingMeeting = false,
  endMeetingDisabled = false,
  audioSource = 'mic',
  meetingActions,
  status,
}: Props) {
  const sourceLabel = {
    mic: '마이크',
    system: '시스템 오디오',
    both: '마이크 + 시스템',
  }[audioSource]

  return (
    <footer
      className="shrink-0 px-3 pt-3 sm:px-5"
      style={{ paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}
    >
      <div className="ui-surface p-2 sm:w-fit sm:max-w-full sm:p-2.5">
        <div role="group" aria-label="녹음 조작" className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
            <button
              type="button"
              onClick={onToggleRecording}
              aria-label={isRecording ? '녹음 중지' : '녹음 시작'}
              aria-pressed={isRecording}
              title={`${isRecording ? '녹음 중지' : '녹음 시작'} · ${sourceLabel}`}
              disabled={isEndingMeeting || (recordingDisabled && !isRecording)}
              className={`ui-button min-h-11 gap-2 px-3 text-xs sm:px-4 sm:text-sm ${isRecording ? 'border-red-200 bg-red-50 text-red-600 hover:!bg-red-100 hover:!text-red-700 dark:border-red-900 dark:bg-red-500/10 dark:text-red-400 dark:hover:!bg-red-500/20 dark:hover:!text-red-300' : 'ui-button-primary'}`}
            >
              <Icon name={isRecording ? 'stop' : 'mic'} className="h-[17px] w-[17px]" />
              {isRecording ? '녹음 중지' : '녹음 시작'}
            </button>
            {isRecording ? (
              <div
                role="status"
                className="hidden h-6 items-center gap-1 px-1 sm:flex"
                aria-label="녹음 중"
              >
                {WAVE_HEIGHTS.map((height, index) => (
                  <span
                    key={index}
                    className="wave-bar w-0.5 rounded-full bg-red-400"
                    style={{
                      height: `${height * (0.4 + Math.min(1, audioLevel) * 0.6)}px`,
                      animationDelay: `${index * 0.12}s`,
                    }}
                  />
                ))}
              </div>
            ) : (
              <span className="hidden min-w-0 truncate text-xs text-muted sm:inline">
                {sourceLabel}
              </span>
            )}
          </div>
          {(onEndMeeting || meetingActions) && (
            <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
              {onEndMeeting && (
                <button
                  type="button"
                  onClick={onEndMeeting}
                  disabled={endMeetingDisabled || isEndingMeeting}
                  aria-label="회의 종료"
                  aria-busy={isEndingMeeting}
                  className="ui-button min-h-11 whitespace-nowrap px-2.5"
                  title={
                    endMeetingDisabled
                      ? '서버에 연결되면 회의를 종료할 수 있습니다'
                      : '녹음을 멈추고 회의 정리'
                  }
                >
                  {isEndingMeeting && (
                    <Icon name="loader" className="hidden h-3.5 w-3.5 animate-spin sm:block" />
                  )}
                  {isEndingMeeting ? (
                    '종료 중…'
                  ) : (
                    <span>
                      <span className="hidden sm:inline">회의 </span>종료
                    </span>
                  )}
                </button>
              )}
              {meetingActions}
            </div>
          )}
        </div>
        {status}
      </div>
    </footer>
  )
}
