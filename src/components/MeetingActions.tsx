'use client'

import type { MeetingEndServerMessage } from '@/lib/websocket'

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
  endState: MeetingEndState | null
  audioPrep: AudioPrepState | null
  onDownloadAudio: () => void
}

/** 서버가 아직 종료를 처리하는 중인지 (녹음 버튼 옆 "미팅 종료" 버튼이 쓴다) */
export function isEnding(s: MeetingEndState | null): boolean {
  return s !== null && (s.phase === 'audio' || s.phase === 'summary')
}

const EMAIL_STATUS_TEXT: Record<NonNullable<MeetingEndServerMessage['status']>, string> = {
  sent: '미팅을 종료하고 메일을 보냈습니다',
  skipped: '미팅을 종료했습니다',
  unverified: '메일 인증이 필요합니다 — 설정에서 인증 메일을 확인하세요',
  already_sent: '미팅을 종료했습니다 (메일은 이미 보냈습니다)',
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
      return s.status ? EMAIL_STATUS_TEXT[s.status] : '미팅을 종료했습니다'
    case 'error':
      return s.error ?? '미팅 종료 처리에 실패했습니다'
  }
}

/** 녹음 파일 다운로드 + 미팅 종료 진행 상태. 녹음 버튼 줄(ControlPanel) 바로 위에 붙는다. */
export default function MeetingActions({
  wsConnected,
  endState,
  audioPrep,
  onDownloadAudio,
}: Props) {
  const preparing = audioPrep?.pending ?? false
  const message = audioPrep?.error ?? (endState ? endStatusText(endState) : null)
  const isError =
    !!audioPrep?.error ||
    endState?.phase === 'error' ||
    (endState?.status !== undefined &&
      !['sent', 'skipped', 'already_sent'].includes(endState.status))

  const btn =
    'flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-medium border transition-all active:scale-95 disabled:opacity-50 disabled:pointer-events-none'

  return (
    <div className="relative z-20 flex items-center justify-end gap-2 px-4 pt-2 flex-shrink-0">
      {message && (
        <p
          role="status"
          className={`mr-auto text-xs truncate ${
            isError ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'
          }`}
        >
          {message}
        </p>
      )}
      <button
        onClick={onDownloadAudio}
        disabled={!wsConnected || preparing}
        className={`${btn} bg-white/70 dark:bg-slate-900/60 border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 hover:bg-white dark:hover:bg-slate-800`}
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
          <path d="M12 3v12" />
          <path d="m7 10 5 5 5-5" />
          <path d="M5 21h14" />
        </svg>
        {preparing ? '준비 중…' : '녹음 파일'}
      </button>
    </div>
  )
}
