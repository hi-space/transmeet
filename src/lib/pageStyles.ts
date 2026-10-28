// 회의 페이지 스타일 — backend/app/page_agent.py PAGE_STYLES, agent/runtime/graph.py STYLES 와 같은 값
export const PAGE_STYLES = [
  {
    value: 'visual',
    label: '시각 회의록',
    description: '핵심 메시지와 전체 구조 그림, 결정·액션·미결, 주제별 설명과 그림을 담습니다.',
  },
  {
    value: 'detailed',
    label: '상세 회의록',
    description:
      '기존 형식입니다. 요약, 결정, 주제별 논의와 발언 인용, 공식 자료 확인, 용어 사전을 담습니다.',
  },
] as const

export type PageStyle = (typeof PAGE_STYLES)[number]['value']
export const DEFAULT_PAGE_STYLE: PageStyle = 'visual'

export function normalizePageStyle(value: unknown): PageStyle {
  return PAGE_STYLES.some((style) => style.value === value)
    ? (value as PageStyle)
    : DEFAULT_PAGE_STYLE
}
