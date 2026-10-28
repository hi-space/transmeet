import definitions from '../../../backend/app/summary_styles.json'
import {
  CUSTOM_SUMMARY_STYLE,
  DEFAULT_SUMMARY_STYLE,
  normalizeSummaryStyle,
  parseSummaryCustom,
  type SummaryCustom,
  type SummaryStyle,
} from '../../../src/lib/summaryStyles'

export { parseSummaryCustom }

export const SUMMARY_SYSTEM_PROMPT = definitions.systemPrompt
export const SUMMARY_MAX_TOKENS = 16384

// 요청 값이 있으면 그것, 없으면 저장값. 내 양식인데 사본이 없으면 detailed.
export function resolveSummaryStyle(
  requested: unknown,
  saved: unknown,
  custom: SummaryCustom | null | undefined
): SummaryStyle {
  const style = normalizeSummaryStyle(requested ?? saved)
  return style === CUSTOM_SUMMARY_STYLE && !custom ? DEFAULT_SUMMARY_STYLE : style
}

function customBody(custom: SummaryCustom): string {
  const lines = [...definitions.customWrapper]
  if (custom.kind === 'example') lines.push(...definitions.exampleWrapper)
  // 긴 견본 뒤에서도 규칙이 약해지지 않게 닫는 태그 바로 뒤에 한 번 더 적는다
  const tail = custom.kind === 'example' ? definitions.exampleReminder : ['']
  return [...lines, '<사용자_양식>', custom.body, '</사용자_양식>', ...tail].join('\n')
}

export function buildSummaryPrompt(
  title: string,
  transcript: string,
  style?: unknown,
  custom?: SummaryCustom | null
): string {
  const head = title.trim() ? `미팅 제목: ${title.trim()}\n\n` : ''
  const resolved = resolveSummaryStyle(style, undefined, custom)
  const body =
    resolved === CUSTOM_SUMMARY_STYLE && custom
      ? customBody(custom)
      : definitions.prompts[resolved].join('\n')
  return `${head}${body}\n---\n${transcript}`
}
