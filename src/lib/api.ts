import type { Meeting, Message, SpeakerRole } from '@/types/meeting'

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
  summary?: string
  summarizedAt?: string
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

export function parseSummary(raw: string): string[] {
  return raw
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#') && !l.startsWith('---'))
    .map((l) =>
      l
        .replace(/^[-*•]\s+/, '')
        .replace(/^\d+\.\s+/, '')
        .trim()
    )
    .filter(Boolean)
}

export function toMeeting(m: ApiMeeting): Meeting {
  return {
    id: m.meetingId,
    title: m.title,
    startedAt: m.createdAt,
    messages: (m.messages ?? []).map(
      (msg): Message => ({
        id: msg.id,
        speaker: VALID_SPEAKERS[msg.speaker] ?? 'speaker1',
        original: msg.originalText,
        translation: msg.translatedText,
        detectedLanguage: msg.detectedLanguage,
        timestamp: msg.timestamp,
      })
    ),
    summary: m.summary ? parseSummary(m.summary) : undefined,
  }
}

// ─── Fetch helper ───────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_ENDPOINT
  if (!base) throw new Error('NEXT_PUBLIC_API_ENDPOINT is not configured')

  const res = await fetch(`${base}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
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
      apiFetch<{ summary: string; meetingId: string }>(`/meetings/${id}/summarize`, {
        method: 'POST',
      }),
  },
  tts: {
    synthesize: (text: string) =>
      apiFetch<TtsResponse>('/tts', {
        method: 'POST',
        body: JSON.stringify({ text, translateFirst: true }),
      }),
  },
}
