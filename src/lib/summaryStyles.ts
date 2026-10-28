export const SUMMARY_STYLES = [
  {
    value: 'detailed',
    label: '자세하게',
    description: '배경, 논의 과정, 결정과 후속 업무를 자세히 정리합니다.',
  },
  {
    value: 'amazon',
    label: 'Amazon 미팅록',
    description:
      '요약(BLUF), 고객 요구, 논의, 결정, 액션 아이템, 리스크를 데이터 중심으로 정리합니다.',
  },
  {
    value: 'decisions',
    label: '의사결정 기록',
    description: '결정마다 맥락, 검토한 대안, 선택 이유, 트레이드오프를 남깁니다.',
  },
  {
    value: 'report',
    label: '공유·보고용',
    description: '요약, 배경, 결정 사항, 향후 일정 순서의 서술형 보고문입니다.',
  },
] as const

export type BuiltinSummaryStyle = (typeof SUMMARY_STYLES)[number]['value']
// 'custom' 은 회의 레코드의 summaryCustom 사본(내 양식)으로 요약한다.
export type SummaryStyle = BuiltinSummaryStyle | 'custom'
export const DEFAULT_SUMMARY_STYLE: SummaryStyle = 'detailed'
export const CUSTOM_SUMMARY_STYLE = 'custom' as const

export type SummaryTemplateKind = 'instruction' | 'example'

// 회의에 복사해 둔 내 양식 사본. 서버가 양식 테이블에서 읽어 저장한다.
export interface SummaryCustom {
  templateId: string
  name: string
  kind: SummaryTemplateKind
  body: string
  templateUpdatedAt: string
}

export interface SummaryTemplate {
  templateId: string
  name: string
  kind: SummaryTemplateKind
  body: string
  createdAt: string
  updatedAt: string
}

// backend/app/summary_prompt.py 의 TEMPLATE_BODY_MAX 와 같다.
export const SUMMARY_TEMPLATE_LIMITS = {
  nameMax: 30,
  instructionMax: 6000,
  exampleMax: 12000,
  perUser: 10,
} as const

export function templateBodyMax(kind: SummaryTemplateKind): number {
  return kind === 'example'
    ? SUMMARY_TEMPLATE_LIMITS.exampleMax
    : SUMMARY_TEMPLATE_LIMITS.instructionMax
}

export function isBuiltinSummaryStyle(value: unknown): value is BuiltinSummaryStyle {
  return SUMMARY_STYLES.some((style) => style.value === value)
}

export function isSummaryStyle(value: unknown): value is SummaryStyle {
  return value === CUSTOM_SUMMARY_STYLE || isBuiltinSummaryStyle(value)
}

export function normalizeSummaryStyle(value: unknown): SummaryStyle {
  return isSummaryStyle(value) ? value : DEFAULT_SUMMARY_STYLE
}

export function summaryStyleLabel(value: unknown, customName?: string): string {
  const style = normalizeSummaryStyle(value)
  if (style === CUSTOM_SUMMARY_STYLE) return customName || '내 양식'
  return SUMMARY_STYLES.find((option) => option.value === style)!.label
}

// 회의 레코드의 summaryCustom 사본. 모양·길이가 틀리면 null — 그때는 detailed 로 만든다.
export function parseSummaryCustom(value: unknown): SummaryCustom | null {
  if (!value || typeof value !== 'object') return null
  const v = value as Record<string, unknown>
  const strings = ['templateId', 'name', 'kind', 'body', 'templateUpdatedAt'].every(
    (k) => typeof v[k] === 'string'
  )
  if (!strings || (v.kind !== 'instruction' && v.kind !== 'example')) return null
  const body = v.body as string
  if (!body.trim() || body.length > templateBodyMax(v.kind) || !v.templateId || !v.name) return null
  return {
    templateId: v.templateId as string,
    name: v.name as string,
    kind: v.kind,
    body,
    templateUpdatedAt: v.templateUpdatedAt as string,
  }
}
