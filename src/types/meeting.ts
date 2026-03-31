export type SpeakerRole = 'speaker1' | 'speaker2' | 'me'

export interface Message {
  id: string
  speaker: SpeakerRole
  original: string
  translation: string
  detectedLanguage?: 'ko' | 'en'
  streamPhase?: 'stt' | 'translating' | 'done'
  timestamp: string
  sentenceCount?: number // 병합된 문장 수 (기본값 1)
}

export interface Meeting {
  id: string
  title: string
  startedAt: string
  messages: Message[]
  messageCount?: number // 목록 API에서 messages 없이 반환되는 경우 사용
  summary?: string // raw markdown
}
