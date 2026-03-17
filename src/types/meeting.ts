export type SpeakerRole = 'speaker1' | 'speaker2' | 'me'

export interface Message {
  id: string
  speaker: SpeakerRole
  original: string
  translation: string
  detectedLanguage?: 'ko' | 'en'
  timestamp: string
}

export interface Meeting {
  id: string
  title: string
  startedAt: string
  messages: Message[]
  summary?: string[]
}
