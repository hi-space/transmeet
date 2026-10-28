import type { Meeting, MeetingBrief, MeetingPage, Message } from '@/types/meeting'
import { getIdToken } from '@/lib/cognito'
import { normalizeSpeaker } from '@/lib/speakers'
import {
  normalizeSummaryStyle,
  parseSummaryCustom,
  type SummaryCustom,
  type SummaryStyle,
  type SummaryTemplate,
  type SummaryTemplateKind,
} from '@/lib/summaryStyles'

// ─── Server-side types ──────────────────────────────────────────────────────

interface ApiMessage {
  id: string
  speaker: string
  originalText: string
  translatedText: string
  detectedLanguage?: 'ko' | 'en'
  targetLanguage?: 'ko' | 'en'
  timestamp: string
}

export interface ApiMeeting extends Partial<MeetingBrief> {
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
  summaryStyle?: SummaryStyle
  summaryGeneratedStyle?: SummaryStyle
  summaryGeneratedLabel?: string
  summaryCustom?: unknown
  summarizedAt?: string
  page?: MeetingPage
}

export interface SummaryTemplateInput {
  name: string
  kind: SummaryTemplateKind
  body: string
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
    if (
      last.speaker === msg.speaker &&
      last.detectedLanguage === msg.detectedLanguage &&
      last.targetLanguage === msg.targetLanguage &&
      silenceMs < MERGE_TIMEOUT_MS
    ) {
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
      speaker: normalizeSpeaker(msg.speaker),
      original: msg.originalText,
      translation: msg.translatedText,
      detectedLanguage: msg.detectedLanguage,
      targetLanguage: msg.targetLanguage,
      timestamp: msg.timestamp,
    })
  )
  // Concurrent translations can finish (and be saved) out of conversation order.
  rawMessages.sort((a, b) => {
    const aTime = Date.parse(a.timestamp)
    const bTime = Date.parse(b.timestamp)
    if (!Number.isFinite(aTime)) return Number.isFinite(bTime) ? 1 : 0
    if (!Number.isFinite(bTime)) return -1
    return aTime - bTime
  })
  const messages = mergeLoadedMessages(rawMessages)
  const briefIndex = rawMessages.findIndex((message) => message.id === m.briefLastMessageId)
  // Saved utterances may merge into fewer display bubbles when a meeting is reopened.
  // Tied timestamps or late saves must not advance the checkpoint beyond its original count.
  const briefMessageCount =
    briefIndex >= 0
      ? Math.min(
          m.briefMessageCount ?? messages.length,
          mergeLoadedMessages(rawMessages.slice(0, briefIndex + 1)).length
        )
      : Math.min(m.briefMessageCount ?? 0, messages.length)
  return {
    id: m.meetingId,
    title: m.title,
    startedAt: m.createdAt,
    messages,
    messageCount: m.messageCount,
    hasAudio: m.hasAudio,
    summary: m.summary ? parseSummary(m.summary) : undefined,
    summaryStyle: normalizeSummaryStyle(m.summaryStyle),
    summaryGeneratedStyle: m.summary ? normalizeSummaryStyle(m.summaryGeneratedStyle) : undefined,
    summaryGeneratedLabel:
      m.summary && m.summaryGeneratedStyle === 'custom' ? m.summaryGeneratedLabel : undefined,
    summaryCustom: parseSummaryCustom(m.summaryCustom) ?? undefined,
    brief: m.brief,
    briefUpdatedAt: m.briefUpdatedAt,
    briefMessageCount,
    briefLastMessageId: m.briefLastMessageId,
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
    summarize: (id: string, summaryStyle?: SummaryStyle) =>
      apiFetch<{
        summary: string
        meetingId: string
        title?: string
        summaryGeneratedStyle?: SummaryStyle
        summaryGeneratedLabel?: string
      }>(`/meetings/${id}/summarize`, {
        method: 'POST',
        body: JSON.stringify({ summaryStyle }),
      }),
    // 내 양식(custom)은 templateId 만 보낸다 — 사본은 서버가 양식 테이블에서 읽어 만든다
    updateSummaryStyle: (id: string, summaryStyle: SummaryStyle, templateId?: string) =>
      apiFetch<{ meetingId: string; summaryStyle: SummaryStyle; summaryCustom?: SummaryCustom }>(
        `/meetings/${id}/summary-style`,
        {
          method: 'PUT',
          body: JSON.stringify({ summaryStyle, ...(templateId ? { templateId } : {}) }),
          signal: AbortSignal.timeout(15_000),
        }
      ),
    brief: (id: string, messages: Pick<Message, 'id' | 'original'>[]) =>
      apiFetch<MeetingBrief & { meetingId: string }>(`/meetings/${id}/summarize`, {
        method: 'POST',
        body: JSON.stringify({ mode: 'brief', messages }),
        signal: AbortSignal.timeout(30_000),
      }),
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
  summaryTemplates: {
    list: () =>
      apiFetch<{ templates: SummaryTemplate[] }>('/me/summary-templates').then((r) => r.templates),
    create: (input: SummaryTemplateInput) =>
      apiFetch<SummaryTemplate>('/me/summary-templates', {
        method: 'POST',
        body: JSON.stringify(input),
      }),
    update: (templateId: string, input: SummaryTemplateInput) =>
      apiFetch<SummaryTemplate>(`/me/summary-templates/${templateId}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      }),
    remove: (templateId: string) =>
      apiFetch<void>(`/me/summary-templates/${templateId}`, { method: 'DELETE' }),
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
