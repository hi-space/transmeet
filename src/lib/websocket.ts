export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

export interface SubtitleServerMessage {
  type: 'subtitle'
  originalText: string
  translatedText: string
  detectedLanguage?: 'ko' | 'en'
  speaker: string
  timestamp: string
}

export interface SubtitleStreamServerMessage {
  type: 'subtitle_stream'
  messageId: string
  phase: 'stt' | 'translating' | 'done'
  speaker?: string
  timestamp: string
  originalText?: string
  partialTranslation?: string
  translatedText?: string
  detectedLanguage?: 'ko' | 'en'
}

export interface ErrorServerMessage {
  type: 'error'
  message: string
  timestamp: string
}

export type WsServerMessage =
  | SubtitleServerMessage
  | SubtitleStreamServerMessage
  | ErrorServerMessage
