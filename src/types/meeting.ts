export type SpeakerRole = 'speaker1' | 'speaker2' | 'me'

export interface Message {
  id: string
  speaker: SpeakerRole
  original: string
  translation: string
  timestamp: string
}

export interface Meeting {
  id: string
  title: string
  startedAt: string
  messages: Message[]
  summary?: string[]
}
