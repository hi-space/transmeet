import type { Meeting, MeetingPage, Message, SpeakerRole } from '@/types/meeting'
import { getIdToken } from '@/lib/cognito'

// ─── Server-side types ──────────────────────────────────────────────────────

interface ApiMessage {
  id: string
  speaker: string
  originalText: string
  translatedText: string
  detectedLanguage?: 'ko' | 'en'
  timestamp: string
}

export interface ApiMeeting {
  meetingId: string
  title: string
  status?: string
  createdAt: string
  updatedAt?: string
  messages?: ApiMessage[]
  messageCount?: number
  /** 녹음이 저장된 미팅 — 발언이 0개여도 빈 미팅으로 정리하지 않는다 */
  hasAudio?: boolean
  summary?: string
  summarizedAt?: string
  page?: MeetingPage
}

/** 미팅 종료 메일 수신 주소(로그인 계정)의 SES 인증 상태 */
export interface EmailStatus {
  email: string
  status: 'verified' | 'pending' | 'none'
}

export interface TtsResponse {
  audioData: string // base64 MP3
  translatedText: string
  format: string
}

// ─── Mapping helpers ────────────────────────────────────────────────────────

const VALID_SPEAKERS: Record<string, SpeakerRole> = {
  speaker1: 'speaker1',
  speaker2: 'speaker2',
  me: 'me',
}

export function parseSummary(raw: string): string {
  return raw.trim()
}

// 녹음 시와 동일한 10초 기준으로 연속 동일 화자 메시지 병합
const MERGE_TIMEOUT_MS = 10000

function mergeLoadedMessages(messages: Message[]): Message[] {
  return messages.reduce<Message[]>((acc, msg) => {
    const last = acc[acc.length - 1]
    if (!last) return [msg]
    const silenceMs = new Date(msg.timestamp).getTime() - new Date(last.timestamp).getTime()
    if (last.speaker === msg.speaker && silenceMs < MERGE_TIMEOUT_MS) {
      acc[acc.length - 1] = {
        ...last,
        original: [last.original, msg.original].filter(Boolean).join(' '),
        translation: [last.translation, msg.translation].filter(Boolean).join(' '),
        timestamp: msg.timestamp,
      }
      return acc
    }
    return [...acc, msg]
  }, [])
}

export function toMeeting(m: ApiMeeting): Meeting {
  const rawMessages = (m.messages ?? []).map(
    (msg): Message => ({
      id: msg.id,
      speaker: VALID_SPEAKERS[msg.speaker] ?? 'speaker1',
      original: msg.originalText,
      translation: msg.translatedText,
      detectedLanguage: msg.detectedLanguage,
      timestamp: msg.timestamp,
    })
  )
  return {
    id: m.meetingId,
    title: m.title,
    startedAt: m.createdAt,
    messages: mergeLoadedMessages(rawMessages),
    messageCount: m.messageCount,
    hasAudio: m.hasAudio,
    summary: m.summary ? parseSummary(m.summary) : undefined,
    page: m.page,
  }
}

// ─── Fetch helper ───────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_ENDPOINT
  if (!base) throw new Error('NEXT_PUBLIC_API_ENDPOINT is not configured')

  const token = await getIdToken()
  const res = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: token }),
      ...options?.headers,
    },
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text}`)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ─── API client ─────────────────────────────────────────────────────────────

export const api = {
  meetings: {
    list: () => apiFetch<ApiMeeting[]>('/meetings'),
    get: (id: string) => apiFetch<ApiMeeting>(`/meetings/${id}`),
    create: (title?: string) =>
      apiFetch<ApiMeeting>('/meetings', {
        method: 'POST',
        body: JSON.stringify({ title }),
      }),
    delete: (id: string) => apiFetch<void>(`/meetings/${id}`, { method: 'DELETE' }),
    summarize: (id: string) =>
      apiFetch<{ summary: string; meetingId: string; title?: string }>(
        `/meetings/${id}/summarize`,
        {
          method: 'POST',
        }
      ),
    generateTitle: (id: string) =>
      apiFetch<{ title: string; meetingId: string }>(`/meetings/${id}/title`, {
        method: 'POST',
      }),
    updateTitle: (id: string, title: string) =>
      apiFetch<{ title: string; meetingId: string }>(`/meetings/${id}/title`, {
        method: 'PUT',
        body: JSON.stringify({ title }),
      }),
  },
  email: {
    status: () => apiFetch<EmailStatus>('/me/email'),
    // 인증 메일 (재)발송 — 받은 메일의 링크를 눌러야 verified 가 된다
    verify: () => apiFetch<EmailStatus>('/me/email/verify', { method: 'POST' }),
  },
  tts: {
    synthesize: (
      text: string,
      engine?: string,
      voiceId?: string,
      translateFirst = true,
      modelId?: string,
      // 번역 방향 (미지정 시 서버 기본값 ko → en)
      langs?: { sourceLang: 'ko' | 'en'; targetLang: 'ko' | 'en' }
    ) =>
      apiFetch<TtsResponse>('/tts', {
        method: 'POST',
        body: JSON.stringify({ text, translateFirst, engine, voiceId, modelId, ...langs }),
      }),
  },
}
