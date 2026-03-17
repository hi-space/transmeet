export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

export interface SubtitleServerMessage {
  type: 'subtitle'
  originalText: string
  translatedText: string
  speaker: string
  timestamp: string
}

export interface ErrorServerMessage {
  type: 'error'
  message: string
  timestamp: string
}

export type WsServerMessage = SubtitleServerMessage | ErrorServerMessage
