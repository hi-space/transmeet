export type SpeakerRole = `speaker${number}` | 'me'

export interface Message {
  id: string
  speaker: SpeakerRole
  original: string
  translation: string
  detectedLanguage?: 'ko' | 'en'
  streamPhase?: 'stt' | 'translating' | 'done'
  timestamp: string
  qaResponse?: string
  qaStreamPhase?: 'streaming' | 'done' | 'error'
}

export interface Meeting {
  id: string
  title: string
  startedAt: string
  messages: Message[]
  messageCount?: number // 목록 API에서 messages 없이 반환되는 경우 사용
  hasAudio?: boolean // 녹음이 저장돼 있으면 발언이 없어도 정리 대상이 아니다
  summary?: string // raw markdown
  page?: MeetingPage // 발행된 미팅 페이지 (없으면 미생성)
}

export interface MeetingPage {
  pageId: string
  url: string
  generatedAt: string
  title?: string
  shareSummary?: string
}
