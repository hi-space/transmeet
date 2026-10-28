import type { SummaryCustom, SummaryStyle } from '@/lib/summaryStyles'

export type SpeakerRole = `speaker${number}` | 'me'

export interface Message {
  id: string
  speaker: SpeakerRole
  original: string
  translation: string
  detectedLanguage?: 'ko' | 'en'
  targetLanguage?: 'ko' | 'en'
  streamPhase?: 'stt' | 'translating' | 'done'
  timestamp: string
  qaResponse?: string
  qaStreamPhase?: 'streaming' | 'done' | 'error'
}

export interface MeetingBrief {
  brief: string
  briefUpdatedAt: string
  briefMessageCount: number
  briefLastMessageId: string
}

export interface Meeting extends Partial<MeetingBrief> {
  id: string
  title: string
  startedAt: string
  messages: Message[]
  messageCount?: number // 목록 API에서 messages 없이 반환되는 경우 사용
  hasAudio?: boolean // 녹음이 저장돼 있으면 발언이 없어도 정리 대상이 아니다
  summary?: string // raw markdown
  summaryStyle?: SummaryStyle // 다음 요약에 사용할 회의별 선택
  summaryGeneratedStyle?: SummaryStyle // 현재 요약을 실제로 생성한 방식
  summaryGeneratedLabel?: string // 내 양식으로 만들었을 때 그 양식 이름
  summaryCustom?: SummaryCustom // 이 회의가 고른 내 양식의 사본
  page?: MeetingPage // 발행된 미팅 페이지 (없으면 미생성)
}

export interface MeetingPage {
  pageId: string
  url: string
  generatedAt: string
  title?: string
  shareSummary?: string
}
