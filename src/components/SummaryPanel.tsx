'use client'

import { useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Icon from '@/components/ui/Icon'
import MarkdownCopyButtons from '@/components/MarkdownCopyButtons'
import {
  DEFAULT_SUMMARY_STYLE,
  SUMMARY_STYLES,
  isBuiltinSummaryStyle,
  normalizeSummaryStyle,
  summaryStyleLabel,
  type SummaryCustom,
  type SummaryStyle,
  type SummaryTemplate,
} from '@/lib/summaryStyles'

const CUSTOM_PREFIX = 'custom:'
const MANAGE_VALUE = '__manage__'

interface Props {
  summary?: string
  hasMessages?: boolean
  onClose?: () => void
  onSummarize?: () => void
  isSummarizing?: boolean
  summaryStyle?: SummaryStyle
  summaryGeneratedStyle?: SummaryStyle
  onStyleChange?: (style: SummaryStyle, templateId?: string) => void
  isSavingStyle?: boolean
  styleError?: string
  // 내 양식 — onManageTemplates 가 없으면(로컬 전용 모드) 내 양식 묶음을 숨긴다
  templates?: SummaryTemplate[]
  templatesLoaded?: boolean // 목록을 받기 전에는 "삭제된 양식"이라고 단정하지 않는다
  summaryCustom?: SummaryCustom
  summaryGeneratedLabel?: string
  onManageTemplates?: () => void
}

export default function SummaryPanel({
  summary,
  hasMessages = false,
  onClose,
  onSummarize,
  isSummarizing,
  summaryStyle = DEFAULT_SUMMARY_STYLE,
  summaryGeneratedStyle = DEFAULT_SUMMARY_STYLE,
  onStyleChange,
  isSavingStyle,
  styleError,
  templates = [],
  templatesLoaded = false,
  summaryCustom,
  summaryGeneratedLabel,
  onManageTemplates,
}: Props) {
  const contentRef = useRef<HTMLDivElement>(null)
  const style = normalizeSummaryStyle(summaryStyle)
  const custom = style === 'custom' ? summaryCustom : undefined
  const selectedValue = custom
    ? `${CUSTOM_PREFIX}${custom.templateId}`
    : isBuiltinSummaryStyle(style)
      ? style
      : DEFAULT_SUMMARY_STYLE
  const currentTemplate = custom && templates.find((t) => t.templateId === custom.templateId)
  // 회의의 사본보다 양식이 새로우면 다시 적용할 수 있게 한다(같은 값 재선택은 onChange 가 안 난다)
  const templateChanged = !!currentTemplate && currentTemplate.updatedAt > custom!.templateUpdatedAt
  const disabled = isSummarizing || isSavingStyle

  const handleChange = (value: string) => {
    if (!onStyleChange) return
    if (value === MANAGE_VALUE) {
      onManageTemplates?.()
      return
    }
    if (value.startsWith(CUSTOM_PREFIX)) {
      const templateId = value.slice(CUSTOM_PREFIX.length)
      onStyleChange('custom', templateId)
      return
    }
    onStyleChange(normalizeSummaryStyle(value))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-panel">
      <div className="grid shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-1 border-b border-subtle px-2 py-1 lg:block lg:border-0 lg:p-0">
        {onStyleChange && (
          <div className="min-w-0 px-1 lg:shrink-0 lg:border-b lg:border-subtle lg:px-3 lg:py-3">
            <label className="flex items-center justify-between gap-3 text-xs font-medium text-ink">
              <span className="sr-only shrink-0 lg:not-sr-only">요약 방식</span>
              <select
                aria-label="요약 방식"
                className="ui-input h-11 min-w-0 flex-1 py-2 text-xs lg:h-auto"
                value={selectedValue}
                disabled={disabled}
                onChange={(event) => handleChange(event.target.value)}
              >
                {SUMMARY_STYLES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                    {option.value === DEFAULT_SUMMARY_STYLE ? ' (기본)' : ''}
                  </option>
                ))}
                {onManageTemplates && (
                  <optgroup label="내 양식">
                    {templates.map((template) => (
                      <option
                        key={template.templateId}
                        value={`${CUSTOM_PREFIX}${template.templateId}`}
                      >
                        {template.name}
                      </option>
                    ))}
                    {custom && !currentTemplate && (
                      <option value={selectedValue}>
                        {custom.name}
                        {templatesLoaded ? ' (삭제된 양식)' : ''}
                      </option>
                    )}
                    <option value={MANAGE_VALUE}>내 양식 관리…</option>
                  </optgroup>
                )}
              </select>
            </label>
            {templateChanged && (
              <p className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                <span>양식이 수정됐습니다 ·</span>
                <button
                  type="button"
                  className="font-medium text-accent-700 underline-offset-2 hover:underline disabled:opacity-50 dark:text-accent-300"
                  disabled={disabled}
                  onClick={() => onStyleChange('custom', custom!.templateId)}
                >
                  최신 내용 적용
                </button>
              </p>
            )}
            {styleError && (
              <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
                {styleError}
              </p>
            )}
          </div>
        )}
        {(summary || onClose) && (
          <div className="flex shrink-0 items-center gap-1 lg:min-h-12 lg:flex-wrap lg:border-b lg:border-subtle lg:px-2.5 lg:py-1.5">
            {summary && (
              <MarkdownCopyButtons
                markdown={summary}
                label="요약 복사"
                contentRef={contentRef}
                compact
              />
            )}
            {onSummarize && (summary || isSummarizing) && (
              <button
                type="button"
                onClick={onSummarize}
                disabled={!hasMessages || isSavingStyle}
                className="ui-icon-button ml-auto h-8 w-8 min-h-8"
                aria-label={isSummarizing ? '요약 다시 시도' : '요약 새로 생성'}
                title={isSummarizing ? '요약 다시 시도' : '요약 새로 생성'}
              >
                <Icon
                  name="refresh"
                  className={`h-3.5 w-3.5 ${isSummarizing ? 'animate-spin' : ''}`}
                />
              </button>
            )}
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="요약 닫기"
                className="ui-icon-button"
              >
                <Icon name="close" />
              </button>
            )}
          </div>
        )}
      </div>
      {summary ? (
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-4">
          <div
            aria-label="생성된 요약 방식"
            className="mb-4 flex items-center gap-1.5 text-[0.6875rem] font-medium text-muted"
          >
            <Icon name="sparkles" className="h-3.5 w-3.5 text-accent-600 dark:text-accent-400" />
            {isSummarizing ? '요약 업데이트 중' : '회의 요약'}
            <span>· {summaryStyleLabel(summaryGeneratedStyle, summaryGeneratedLabel)}</span>
          </div>
          <div
            ref={contentRef}
            className="prose max-w-none break-words text-sm leading-reading dark:prose-invert [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 prose-strong:text-slate-800 dark:prose-strong:text-slate-100"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
            {isSummarizing && (
              <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse rounded-sm bg-accent-500" />
            )}
          </div>
          <div aria-hidden="true" className="h-24 lg:hidden" />
        </div>
      ) : isSummarizing ? (
        <div className="ui-empty" role="status">
          <div className="ui-empty-icon">
            <Icon name="loader" className="h-6 w-6 animate-spin" />
          </div>
          <p className="text-sm font-medium text-ink">회의를 정리하고 있습니다</p>
          <p className="text-xs">요약이 생성되면 여기에 표시됩니다</p>
        </div>
      ) : (
        <div className="ui-empty">
          <div className="ui-empty-icon">
            <Icon name="summary" className="h-6 w-6" />
          </div>
          <p className="text-sm font-medium text-ink">아직 요약이 없습니다</p>
          <p className="text-xs">
            {hasMessages ? '대화에서 핵심 내용을 정리하세요' : '대화가 쌓이면 요약할 수 있습니다'}
          </p>
          {onSummarize && (
            <button
              type="button"
              onClick={onSummarize}
              disabled={!hasMessages || isSavingStyle}
              className="ui-button ui-button-primary mt-3"
            >
              <Icon name="sparkles" />
              요약 생성
            </button>
          )}
        </div>
      )}
    </div>
  )
}
