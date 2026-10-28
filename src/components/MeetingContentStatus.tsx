import Icon from '@/components/ui/Icon'

interface Props {
  status: 'loading' | 'error'
  loadingMessage?: string
  onRetry: () => void
}

export default function MeetingContentStatus({
  status,
  loadingMessage = '대화 내용을 불러오는 중…',
  onRetry,
}: Props) {
  if (status === 'error') {
    return (
      <div className="ui-empty" role="alert">
        <div className="ui-empty-icon">
          <Icon name="refresh" className="h-6 w-6" />
        </div>
        <p className="text-sm font-medium text-ink">회의 내용을 불러오지 못했습니다</p>
        <p className="text-xs">잠시 후 다시 시도해 주세요</p>
        <button type="button" onClick={onRetry} className="ui-button ui-button-secondary mt-2">
          <Icon name="refresh" className="h-3.5 w-3.5" />
          다시 시도
        </button>
      </div>
    )
  }

  return (
    <div className="ui-empty" role="status">
      <div className="ui-empty-icon">
        <Icon name="message" className="h-6 w-6" />
      </div>
      <p className="text-sm font-medium text-ink">{loadingMessage}</p>
      <div
        role="progressbar"
        aria-label={loadingMessage}
        className="mt-2 h-1.5 w-48 max-w-full overflow-hidden rounded-full bg-accent-100 dark:bg-accent-950"
      >
        <div className="meeting-load-progress h-full w-1/3 rounded-full bg-accent-600 dark:bg-accent-400" />
      </div>
    </div>
  )
}
