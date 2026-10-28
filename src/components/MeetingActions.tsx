'use client'

import type { MeetingEndServerMessage } from '@/lib/websocket'
import Icon from '@/components/ui/Icon'

export type MeetingEndState = Pick<MeetingEndServerMessage, 'phase' | 'status' | 'error'> & {
  meetingId: string
}

export interface AudioPrepState {
  meetingId: string
  pending: boolean
  error?: string
}

interface Props {
  wsConnected: boolean
  audioPrep: AudioPrepState | null
  onDownloadAudio: () => void
}

interface StatusProps {
  endState: MeetingEndState | null
  audioPrep: AudioPrepState | null
}

/** 서버가 아직 종료를 처리하는 중인지 (녹음 버튼 옆 "회의 종료" 버튼이 쓴다) */
export function isEnding(s: MeetingEndState | null): boolean {
  return s !== null && (s.phase === 'audio' || s.phase === 'summary')
}

const EMAIL_STATUS_TEXT: Record<NonNullable<MeetingEndServerMessage['status']>, string> = {
  sent: '회의를 종료하고 메일을 보냈습니다',
  skipped: '회의를 종료했습니다',
  unverified: '메일 인증이 필요합니다 — 설정에서 인증 메일을 확인하세요',
  already_sent: '회의를 종료했습니다 (메일은 이미 보냈습니다)',
  no_email: '계정 이메일이 확인되지 않아 메일을 보내지 못했습니다',
  failed: '메일 발송에 실패했습니다. 다시 종료하면 재시도합니다',
}

function endStatusText(s: MeetingEndState): string {
  switch (s.phase) {
    case 'audio':
      return '녹음 파일 정리 중…'
    case 'summary':
      return '요약 만드는 중…'
    case 'email':
    case 'done':
      return s.status ? EMAIL_STATUS_TEXT[s.status] : '회의를 종료했습니다'
    case 'error':
      return s.error ?? '회의 종료 처리에 실패했습니다'
  }
}

/** Render feedback within the recording controls only when there is something to report. */
export function MeetingStatus({ endState, audioPrep }: StatusProps) {
  const message = audioPrep?.error ?? (endState ? endStatusText(endState) : null)
  const isError =
    !!audioPrep?.error ||
    endState?.phase === 'error' ||
    (endState?.status !== undefined &&
      !['sent', 'skipped', 'already_sent'].includes(endState.status))

  if (!message) return null

  return (
    <p
      role={isError ? 'alert' : 'status'}
      className={`mt-2 min-w-0 break-words border-t border-subtle px-1 pt-2 text-xs leading-relaxed ${
        isError ? 'text-amber-700 dark:text-amber-400' : 'text-muted'
      }`}
    >
      {message}
    </p>
  )
}

/** A secondary recording action, placed alongside start/stop and meeting end. */
export default function MeetingActions({ wsConnected, audioPrep, onDownloadAudio }: Props) {
  const preparing = audioPrep?.pending ?? false

  return (
    <button
      type="button"
      onClick={onDownloadAudio}
      disabled={!wsConnected || preparing}
      aria-label="녹음 다운로드"
      aria-busy={preparing}
      title={preparing ? '녹음 파일 준비 중' : '녹음 다운로드'}
      className="ui-button ui-button-secondary min-h-11 min-w-11 gap-1.5 px-2.5"
    >
      <Icon
        name={preparing ? 'loader' : 'download'}
        className={`h-4 w-4 ${preparing ? 'animate-spin' : ''}`}
      />
      <span className="hidden sm:inline">{preparing ? '준비 중…' : '다운로드'}</span>
    </button>
  )
}
