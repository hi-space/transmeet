'use client'

import { useState } from 'react'
import type { SummaryTemplateInput } from '@/lib/api'
import {
  SUMMARY_TEMPLATE_LIMITS,
  templateBodyMax,
  type SummaryTemplate,
  type SummaryTemplateKind,
} from '@/lib/summaryStyles'

interface Props {
  templates: SummaryTemplate[]
  error?: string
  onSave: (input: SummaryTemplateInput, templateId?: string) => Promise<unknown>
  onRemove: (templateId: string) => Promise<unknown>
}

const KINDS: { value: SummaryTemplateKind; label: string; placeholder: string }[] = [
  {
    value: 'instruction',
    label: '지시문 작성',
    placeholder:
      '예) 섹션은 이슈 / 원인 / 조치 / 담당. 각 이슈는 표로, 조치에는 기한을 붙여 주세요.',
  },
  {
    value: 'example',
    label: '기존 회의록 붙여넣기',
    placeholder:
      '팀에서 쓰던 회의록을 그대로 붙여 넣으세요. 구조와 톤만 따르고 내용은 옮기지 않습니다.',
  },
]

type Draft = SummaryTemplateInput & { templateId?: string }

const EMPTY: Draft = { name: '', kind: 'instruction', body: '' }

// 설정 > 요약 양식. 목록 + 편집기 하나 — 편집 중인 양식만 펼친다.
export default function SummaryTemplateSettings({ templates, error, onSave, onRemove }: Props) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState<string>()

  const max = draft ? templateBodyMax(draft.kind) : 0
  const nameLength = draft?.name.trim().length ?? 0
  const valid =
    !!draft &&
    nameLength > 0 &&
    nameLength <= SUMMARY_TEMPLATE_LIMITS.nameMax &&
    draft.body.trim().length > 0 &&
    draft.body.length <= max
  const full = templates.length >= SUMMARY_TEMPLATE_LIMITS.perUser

  const run = async (action: () => Promise<unknown>, failure: string) => {
    setBusy(true)
    setSaveError(undefined)
    try {
      await action()
      setDraft(null)
    } catch {
      setSaveError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <div>
        <p className="ui-setting-label">내 양식</p>
        <p className="ui-setting-hint">
          요약 패널의 요약 방식에서 고를 수 있는 나만의 양식입니다. 최대{' '}
          {SUMMARY_TEMPLATE_LIMITS.perUser}개까지 저장됩니다.
        </p>
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {templates.length > 0 && (
        <ul className="divide-y divide-subtle rounded-lg border border-subtle">
          {templates.map((template) => (
            <li key={template.templateId} className="flex items-center gap-2 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{template.name}</p>
                <p className="text-[0.6875rem] text-muted">
                  {KINDS.find((k) => k.value === template.kind)?.label} · {template.body.length}자
                </p>
              </div>
              <button
                type="button"
                className="ui-button ui-button-secondary px-2.5 text-xs"
                disabled={busy}
                onClick={() => {
                  setSaveError(undefined)
                  setDraft({ ...template })
                }}
              >
                수정
              </button>
              <button
                type="button"
                className="ui-button ui-button-secondary px-2.5 text-xs"
                disabled={busy}
                aria-label={`${template.name} 삭제`}
                onClick={() => {
                  if (
                    !window.confirm(
                      `'${template.name}' 양식을 삭제할까요? 이 양식을 고른 회의는 저장된 사본으로 계속 요약됩니다.`
                    )
                  )
                    return
                  void run(() => onRemove(template.templateId), '양식을 삭제하지 못했습니다.')
                }}
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      )}

      {!draft ? (
        <button
          type="button"
          className="ui-button ui-button-secondary w-full"
          disabled={full}
          onClick={() => {
            setSaveError(undefined)
            setDraft({ ...EMPTY })
          }}
        >
          새 양식
        </button>
      ) : (
        <form
          className="space-y-3 rounded-lg border border-subtle p-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (!valid || busy) return
            const { templateId, ...input } = draft
            void run(
              () => onSave({ ...input, name: input.name.trim() }, templateId),
              '양식을 저장하지 못했습니다.'
            )
          }}
        >
          <label className="block space-y-1.5">
            <span className="ui-setting-label">양식 이름</span>
            <input
              aria-label="양식 이름"
              className="ui-input w-full text-sm"
              value={draft.name}
              maxLength={SUMMARY_TEMPLATE_LIMITS.nameMax}
              placeholder="예) 주간 팀 회의"
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </label>
          <fieldset className="space-y-1.5">
            <legend className="ui-setting-label mb-1.5">만드는 방법</legend>
            <div className="grid grid-cols-2 gap-1.5">
              {KINDS.map((kind) => (
                <button
                  key={kind.value}
                  type="button"
                  aria-pressed={draft.kind === kind.value}
                  onClick={() => setDraft({ ...draft, kind: kind.value })}
                  className={`ui-button min-w-0 px-1.5 text-xs ${draft.kind === kind.value ? 'ui-button-tinted border-accent-200 dark:border-accent-700' : 'ui-button-secondary'}`}
                >
                  {kind.label}
                </button>
              ))}
            </div>
          </fieldset>
          <label className="block space-y-1.5">
            <span className="flex items-baseline justify-between">
              <span className="ui-setting-label">양식 내용</span>
              <span
                className={`text-[0.6875rem] tabular-nums ${draft.body.length > max ? 'text-red-600 dark:text-red-400' : 'text-muted'}`}
              >
                {draft.body.length} / {max}
              </span>
            </span>
            <textarea
              aria-label="양식 내용"
              className="ui-input min-h-40 w-full resize-y text-sm leading-relaxed"
              value={draft.body}
              placeholder={KINDS.find((k) => k.value === draft.kind)?.placeholder}
              onChange={(event) => setDraft({ ...draft, body: event.target.value })}
            />
          </label>
          <p className="ui-setting-hint">
            사실은 항상 대화에서만 가져오고, 없는 담당·기한은 추측하지 않습니다. 메일에서는
            제목·불릿·표·문단만 그대로 보입니다.
          </p>
          {saveError && (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {saveError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="ui-button ui-button-secondary"
              disabled={busy}
              onClick={() => setDraft(null)}
            >
              취소
            </button>
            <button type="submit" className="ui-button ui-button-primary" disabled={!valid || busy}>
              저장
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
