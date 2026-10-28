'use client'

import type { SttStatus } from '@/lib/sttProviders'
import Icon from '@/components/ui/Icon'

const LABELS: Record<SttStatus['status'], string> = {
  checking: '엔진 상태 확인 중',
  sleeping: '대기 중 · GPU 0대',
  starting: '엔진 준비 중',
  ready: '사용 가능',
  cooling: '대기 상태로 전환 중',
  unavailable: '엔진 설정 필요',
  error: '엔진 상태 확인 필요',
  unknown: '엔진 상태 재확인 중',
}

export default function SttStatusPanel({
  state,
  onPrepare,
  connected,
  compact = false,
}: {
  state?: SttStatus
  onPrepare?: () => void
  connected: boolean
  compact?: boolean
}) {
  const status = state?.status ?? 'checking'
  const waiting = status === 'starting' || status === 'cooling' || status === 'checking'
  return (
    <div
      role="status"
      aria-live="polite"
      className={compact ? 'mt-2 px-1 text-xs text-muted' : 'rounded-xl border border-subtle p-3'}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-xs font-medium">
          {waiting && <Icon name="loader" className="h-3.5 w-3.5 animate-spin" />}
          {connected ? LABELS[status] : '서버 연결 후 엔진을 준비할 수 있습니다'}
        </span>
        {!compact && status !== 'ready' && (
          <button
            type="button"
            className="ui-button ui-button-secondary min-h-9 px-3 text-xs"
            disabled={!connected || waiting}
            onClick={onPrepare}
          >
            {waiting ? '준비 중' : '엔진 준비하기'}
          </button>
        )}
      </div>
      {!compact && (
        <p className="mt-2 text-xs leading-relaxed text-muted">
          {state?.message ??
            (status === 'ready'
              ? '녹음을 시작할 수 있습니다. 사용하지 않으면 자동으로 대기 상태가 됩니다.'
              : '대기 상태에서는 GPU를 사용하지 않습니다. 기동에는 몇 분 걸릴 수 있습니다.')}
        </p>
      )}
    </div>
  )
}
