import type { MeetingPage } from '@/types/meeting'
import type { SummaryStyle } from '@/lib/summaryStyles'

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
  phase: 'stt_partial' | 'stt' | 'translating' | 'done'
  speaker?: string
  timestamp: string
  originalText?: string
  partialTranslation?: string
  translatedText?: string
  detectedLanguage?: 'ko' | 'en'
  targetLanguage?: 'ko' | 'en'
}

export interface TtsStreamServerMessage {
  type: 'tts_stream'
  messageId: string
  phase: 'translating' | 'done' | 'error'
  partialText?: string
  translatedText?: string
  audioData?: string
  timestamp: string
}

export interface SummaryStreamServerMessage {
  type: 'summary_stream'
  meetingId?: string
  phase: 'delta' | 'done' | 'error'
  summaryStyle?: SummaryStyle
  summaryLabel?: string // 내 양식 이름(custom 일 때만)
  text?: string
  summary?: string
  title?: string
  error?: string
}

export interface QaStreamServerMessage {
  type: 'qa_stream'
  messageId: string
  phase: 'delta' | 'done' | 'error'
  text?: string
  answer?: string
  error?: string
}

export interface PageStreamServerMessage {
  type: 'page_stream'
  meetingId?: string
  op?: 'unpublish' // 공유 해제 요청에 대한 이벤트
  phase: 'status' | 'delta' | 'done' | 'error' | 'unpublished'
  started?: boolean // 서버가 시작한 자동 생성도 진행 상태 추적을 시작한다
  text?: string
  page?: MeetingPage
  error?: string
}

/** 미팅 종료 진행. email 단계의 status 로 메일 결과를 안다 */
export interface MeetingEndServerMessage {
  type: 'meeting_end'
  meetingId?: string
  phase: 'audio' | 'summary' | 'email' | 'done' | 'error'
  status?: 'sent' | 'skipped' | 'unverified' | 'already_sent' | 'no_email' | 'failed'
  error?: string
}

/** prepare_audio 응답 — url(5분 유효) 또는 error */
export interface AudioReadyServerMessage {
  type: 'audio_ready'
  meetingId: string
  url?: string
  error?: string
}

export interface ErrorServerMessage {
  type: 'error'
  message: string
  timestamp: string
}

export interface PongServerMessage {
  type: 'pong'
  timestamp: string
}

export type WsServerMessage =
  | SttStatusServerMessage
  | SubtitleServerMessage
  | SubtitleStreamServerMessage
  | TtsStreamServerMessage
  | SummaryStreamServerMessage
  | QaStreamServerMessage
  | PageStreamServerMessage
  | MeetingEndServerMessage
  | AudioReadyServerMessage
  | ErrorServerMessage
  | PongServerMessage
import type { SttStatus } from '@/lib/sttProviders'

export interface SttStatusServerMessage extends SttStatus {
  type: 'stt_status'
}
