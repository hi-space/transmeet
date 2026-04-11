'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import Header from '@/components/Header'
import MeetingSidebar from '@/components/MeetingSidebar'
import ChatArea from '@/components/ChatArea'
import SummaryPanel from '@/components/SummaryPanel'
import ControlPanel from '@/components/ControlPanel'
import SettingsPanel from '@/components/SettingsPanel'
import QuickTranslatePopup from '@/components/QuickTranslatePopup'
import AuthScreen from '@/components/AuthScreen'
import MobileTabBar from '@/components/MobileTabBar'
import NotesArea from '@/components/NotesArea'
import VoiceArea from '@/components/VoiceArea'
import { Meeting, Message } from '@/types/meeting'
import { useAudioCapture } from '@/hooks/useAudioCapture'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSettings } from '@/hooks/useSettings'
import { useSummaryResize } from '@/hooks/useSummaryResize'
import { useAuth } from '@/context/AuthContext'
import type { WsServerMessage } from '@/lib/websocket'
import { api, toMeeting, parseSummary } from '@/lib/api'

const HAS_COGNITO = !!process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID

// ─── Dev fallback when API is not configured ────────────────────────────────

const HAS_API = !!process.env.NEXT_PUBLIC_API_ENDPOINT

const MOCK_MEETINGS: Meeting[] = [
  {
    id: 'm1',
    title: 'Product Review',
    startedAt: '2026-03-17T09:00:00',
    messages: [
      {
        id: '1',
        speaker: 'speaker1',
        original: "Good morning everyone, let's get started with today's product review.",
        translation: '좋은 아침입니다 여러분, 오늘 제품 리뷰를 시작하겠습니다.',
        timestamp: '2026-03-17T09:01:00',
      },
      {
        id: '2',
        speaker: 'me',
        original: 'Good morning! Ready when you are.',
        translation: '좋은 아침이에요! 언제든지 준비됐습니다.',
        timestamp: '2026-03-17T09:01:30',
      },
      {
        id: '3',
        speaker: 'speaker2',
        original: "I've prepared the Q1 dashboard. Let me share my screen.",
        translation: '1분기 대시보드를 준비했습니다. 화면을 공유할게요.',
        timestamp: '2026-03-17T09:02:00',
      },
    ],
    summary: `## 회의 개요\n- 주제: Q1 제품 리뷰\n- 참석자: Speaker 1 (발표자), Me (참석자), Speaker 2\n\n## 주요 논의 사항\n- Q1 대시보드 공유 및 전환율 검토\n\n## 결정 사항\n- Q1 전환율 12% 증가 확인\n- 온보딩 재설계가 주요 성과 요인으로 인정\n\n## Action Items\n- Q2 목표: 전환율 최적화 진행`,
  },
  {
    id: 'm2',
    title: 'Design Sync',
    startedAt: '2026-03-16T14:00:00',
    messages: [
      {
        id: '4',
        speaker: 'speaker1',
        original: 'The new design system looks fantastic!',
        translation: '새 디자인 시스템이 정말 훌륭합니다!',
        timestamp: '2026-03-16T14:01:00',
      },
    ],
  },
  {
    id: 'm3',
    title: 'Team Standup',
    startedAt: '2026-03-15T10:00:00',
    messages: [],
  },
]

// ─── Audio helpers ──────────────────────────────────────────────────────────

function playBase64Audio(
  base64: string,
  onAudio?: (audio: HTMLAudioElement) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const binary = atob(base64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const blob = new Blob([bytes], { type: 'audio/mp3' })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    onAudio?.(audio)
    audio.onended = () => {
      URL.revokeObjectURL(url)
      resolve()
    }
    audio.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Audio playback failed'))
    }
    audio.play().catch(reject)
  })
}

// ─── Partial 번역 헬퍼 ────────────────────────────────────────────────────────

/**
 * 텍스트에서 완성된 문장(마지막 .?! 까지)을 반환.
 * 불완전한 끝 문장은 제외.
 * 예: "Hello. How are you" → "Hello."
 *     "Hello. How are you?" → "Hello. How are you?"
 */
function extractCompleteSentences(text: string): string {
  const match = text.match(/^([\s\S]*[.?!])(?:\s|$)/)
  return match ? match[1].trim() : ''
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Home() {
  const { user, isLoading: authLoading, setUser, logout } = useAuth()
  const { settings, updateSettings } = useSettings()
  const { width: summaryWidth, handleMouseDown: handleSummaryResize } = useSummaryResize(384)

  const [meetings, setMeetings] = useState<Meeting[]>(HAS_API ? [] : MOCK_MEETINGS)
  const [activeMeetingId, setActiveMeetingId] = useState(HAS_API ? '' : MOCK_MEETINGS[0].id)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickTranslateOpen, setQuickTranslateOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'voice' | 'notes'>('voice')
  const [hasNewVoice, setHasNewVoice] = useState(false)
  const [hasNewNotes, setHasNewNotes] = useState(false)
  const [notesCollapsed, setNotesCollapsed] = useState(false)
  const [ttsInput, setTtsInput] = useState('')
  const [isSummarizing, setIsSummarizing] = useState(false)
  const [isTtsPending, setIsTtsPending] = useState(false)
  const [pendingTranscript, setPendingTranscript] = useState<{
    messageId: string
    text: string
    speaker: string
    translation?: string
  } | null>(null)
  const [isLoadingMeetings, setIsLoadingMeetings] = useState(HAS_API)
  const [isCreatingMeeting, setIsCreatingMeeting] = useState(false)
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null)
  const [isMessageLoading, setIsMessageLoading] = useState(false)

  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const msgAudioRef = useRef<HTMLAudioElement | null>(null)
  // partial 번역 carry over: handleWsMessage가 memoize되어 pendingTranscript가 deps에 없으므로 ref로 동기화
  const pendingTranscriptRef = useRef<typeof pendingTranscript>(null)

  // Track last auto-summarized message count per meeting
  const lastSummarizedCountRef = useRef<Record<string, number>>({})
  // Stable ref for handleSummarize to avoid stale closure in timers
  const handleSummarizeRef = useRef<() => Promise<void>>(() => Promise.resolve())
  // partial 번역: 마지막으로 번역 요청한 완성 문장 텍스트
  const lastTranslatedPartialRef = useRef<string>('')
  // realtime 모드 스로틀: 1.5초 간격으로 최신 partial 번역
  const partialThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingPartialRef = useRef<string>('')
  // sendTranslate ref: handleWsMessage보다 먼저 선언되어야 하므로 ref 패턴 사용
  const sendTranslateRef = useRef<
    (
      messageId: string,
      originalText: string,
      speaker: string,
      sourceLang?: string,
      targetLang?: string,
      modelId?: string
    ) => void
  >(() => {})
  // Safety: reset isSummarizing if WS done/error event is never received
  const isSummarizingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Ref-based isSummarizing: stuck 감지 및 force reset에 사용 (state와 동기화)
  const isSummarizingRef = useRef(false)
  // Per-message translation timeouts: auto-unblock messages stuck in stt/translating
  const translationTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  // STT health watchdog: last time any STT event was received (for stuck session detection)
  const lastSttActivityTimeRef = useRef<number>(Date.now())
  // 말풍선 병합: 병합된 신규 messageId → 기존 말풍선 id 매핑
  const mergeAliasRef = useRef<Map<string, string>>(new Map())

  const activeMeeting = meetings.find((m) => m.id === activeMeetingId) ?? meetings[0]

  // 탭 배지: 비활성 탭에 새 메시지 도착 시 표시
  useEffect(() => {
    const msgs = activeMeeting?.messages ?? []
    if (msgs.length === 0) return
    const lastMsg = msgs[msgs.length - 1]
    if (lastMsg.speaker !== 'me' && activeTab === 'notes') setHasNewVoice(true)
    if (lastMsg.speaker === 'me' && activeTab === 'voice') setHasNewNotes(true)
  }, [activeMeeting?.messages?.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // pendingTranscript를 ref로 동기화 — handleWsMessage 클로저에서 stale 없이 읽기 위해
  useEffect(() => {
    pendingTranscriptRef.current = pendingTranscript
  }, [pendingTranscript])

  // isSummarizing ref 동기화
  useEffect(() => {
    isSummarizingRef.current = isSummarizing
  }, [isSummarizing])

  // ─── Task #10: Load meetings on mount ──────────────────────────────────────

  const loadMeetingMessages = useCallback(async (id: string) => {
    try {
      const full = await api.meetings.get(id)
      const meeting = toMeeting(full)
      setMeetings((prev) => prev.map((m) => (m.id === id ? meeting : m)))
    } catch {
      // silent — sidebar still shows the meeting
    }
  }, [])

  useEffect(() => {
    if (!HAS_API) return

    api.meetings
      .list()
      .then(async (list) => {
        if (list.length === 0) {
          return api.meetings.create('새 회의').then((m) => {
            const meeting = toMeeting(m)
            setMeetings([meeting])
            setActiveMeetingId(meeting.id)
          })
        }
        const mapped = list.map(toMeeting)
        setMeetings(mapped)
        setActiveMeetingId(mapped[0].id)
        // list API omits messages — fetch full data for the first (active) meeting
        await loadMeetingMessages(mapped[0].id)
      })
      .catch(() => {
        // API unavailable — fall back to mock data
        setMeetings(MOCK_MEETINGS)
        setActiveMeetingId(MOCK_MEETINGS[0].id)
      })
      .finally(() => setIsLoadingMeetings(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ─── Task #10: Create new meeting ──────────────────────────────────────────

  const handleNewMeeting = useCallback(async () => {
    if (isCreatingMeeting) return
    setIsCreatingMeeting(true)

    if (!HAS_API) {
      const now = new Date()
      const mock: Meeting = {
        id: `m${Date.now()}`,
        title: `새 회의 ${now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`,
        startedAt: now.toISOString(),
        messages: [],
      }
      setMeetings((prev) => [mock, ...prev])
      setActiveMeetingId(mock.id)
      setSidebarOpen(false)
      setIsCreatingMeeting(false)
      return
    }

    try {
      const m = await api.meetings.create()
      const meeting = toMeeting(m)
      setMeetings((prev) => [meeting, ...prev])
      setActiveMeetingId(meeting.id)
      setSidebarOpen(false)
    } finally {
      setIsCreatingMeeting(false)
    }
  }, [isCreatingMeeting])

  // ─── Issue #33: Generate meeting title ────────────────────────────────────

  const [generatingTitleId, setGeneratingTitleId] = useState<string | null>(null)

  const handleGenerateTitle = useCallback(
    async (id: string) => {
      if (!HAS_API || generatingTitleId) return
      setGeneratingTitleId(id)
      try {
        const { title } = await api.meetings.generateTitle(id)
        setMeetings((prev) => prev.map((m) => (m.id === id ? { ...m, title } : m)))
      } catch {
        // Silent — user can retry
      } finally {
        setGeneratingTitleId(null)
      }
    },
    [generatingTitleId]
  )

  // ─── Issue #30: Delete meeting ─────────────────────────────────────────────

  const handleDeleteMeeting = useCallback(
    async (id: string) => {
      if (HAS_API) {
        try {
          await api.meetings.delete(id)
        } catch {
          return
        }
      }
      setMeetings((prev) => prev.filter((m) => m.id !== id))
      if (activeMeetingId === id) {
        const next = meetings.find((m) => m.id !== id)
        if (next) {
          setActiveMeetingId(next.id)
          if (HAS_API) loadMeetingMessages(next.id)
        }
      }
    },
    [activeMeetingId, meetings, loadMeetingMessages]
  )

  // ─── Task #5: WebSocket subtitle handler ───────────────────────────────────

  const handleWsMessage = useCallback(
    (msg: WsServerMessage) => {
      if (msg.type === 'subtitle') {
        // Legacy non-streaming path
        const newMsg: Message = {
          id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          speaker: (msg.speaker as Message['speaker']) ?? 'speaker1',
          original: msg.originalText,
          translation: msg.translatedText,
          detectedLanguage: msg.detectedLanguage,
          timestamp: msg.timestamp,
        }
        setMeetings((prev) =>
          prev.map((m) =>
            m.id === activeMeetingId ? { ...m, messages: [...m.messages, newMsg] } : m
          )
        )
      } else if (msg.type === 'subtitle_stream') {
        lastSttActivityTimeRef.current = Date.now()
        const SILENCE_TIMEOUT_MS = settings.silenceTimeout
        if (msg.phase === 'stt_partial') {
          // Word-by-word Transcribe partial — show in pending bubble, not committed messages
          const partialText = msg.originalText ?? ''
          const partialSpeaker = msg.speaker ?? 'speaker1'
          setPendingTranscript((prev) => ({
            messageId: msg.messageId,
            text: partialText,
            speaker: partialSpeaker,
            translation: prev?.translation, // 기존 번역 유지
          }))

          const srcLang = settings.sourceLang !== 'auto' ? settings.sourceLang : 'en'

          if (settings.partialTranslationMode === 'realtime') {
            // 스로틀: 타이머가 없을 때만 새 타이머 등록 (1.5초 간격)
            // partial마다 최신 텍스트를 pendingPartialRef에 저장하고,
            // 타이머 만료 시 최신 텍스트로 번역 — 연속 발화 중에도 주기적으로 번역됨
            if (partialText) {
              pendingPartialRef.current = partialText
              if (!partialThrottleRef.current) {
                partialThrottleRef.current = setTimeout(() => {
                  partialThrottleRef.current = null
                  const text = pendingPartialRef.current
                  if (text && text !== lastTranslatedPartialRef.current) {
                    lastTranslatedPartialRef.current = text
                    sendTranslateRef.current(
                      '__pending__',
                      text,
                      partialSpeaker,
                      srcLang,
                      settings.targetLang,
                      settings.translationModel
                    )
                  }
                }, settings.partialThrottleMs)
              }
            }
          } else {
            // sentence: 문장 종결 부호 감지 → 증분 번역
            const allComplete = extractCompleteSentences(partialText)
            const lastTranslated = lastTranslatedPartialRef.current
            if (allComplete && allComplete !== lastTranslated) {
              if (allComplete.startsWith(lastTranslated)) {
                // 새로 완성된 문장만 번역 후 기존 번역에 append
                const newPart = allComplete.slice(lastTranslated.length).trim()
                if (newPart) {
                  lastTranslatedPartialRef.current = allComplete
                  sendTranslateRef.current(
                    '__pending_append__',
                    newPart,
                    partialSpeaker,
                    srcLang,
                    settings.targetLang,
                    settings.translationModel
                  )
                }
              } else {
                // Transcribe가 이전 텍스트 수정 → 전체 재번역
                lastTranslatedPartialRef.current = allComplete
                setPendingTranscript((prev) => (prev ? { ...prev, translation: '' } : prev))
                sendTranslateRef.current(
                  '__pending__',
                  allComplete,
                  partialSpeaker,
                  srcLang,
                  settings.targetLang,
                  settings.translationModel
                )
              }
            }
          }
          return
        }
        // 번역 고착 방지: displayId에 대해 15초 타임아웃 설정
        const scheduleTranslationTimeout = (displayId: string) => {
          const existing = translationTimeoutsRef.current.get(displayId)
          if (existing) clearTimeout(existing)
          const t = setTimeout(() => {
            translationTimeoutsRef.current.delete(displayId)
            setMeetings((prev) =>
              prev.map((m) =>
                m.id === activeMeetingId
                  ? {
                      ...m,
                      messages: m.messages.map((existing) =>
                        existing.id === displayId &&
                        (existing.streamPhase === 'stt' || existing.streamPhase === 'translating')
                          ? { ...existing, streamPhase: 'done' as const }
                          : existing
                      ),
                    }
                  : m
              )
            )
          }, 15000)
          translationTimeoutsRef.current.set(displayId, t)
        }

        if (msg.phase === 'stt') {
          // Final sentence from Transcribe — clear pending bubble
          // partial 번역을 carry over: pending bubble에 쌓인 번역을 새 말풍선 초기값으로 설정
          const carryoverTranslation = pendingTranscriptRef.current?.translation ?? ''
          lastTranslatedPartialRef.current = ''
          setPendingTranscript(null)
          const streamSpeaker = (msg.speaker as Message['speaker']) ?? 'speaker1'
          setMeetings((prev) =>
            prev.map((m) => {
              if (m.id !== activeMeetingId) return m
              // 병합 조건:
              // - 이전 말풍선이 미완성 문장(종결부호 없음) → 무조건 병합
              // - 이전 말풍선이 완성 문장 → 같은 화자 + silenceTimeout 이내일 때만 병합
              const lastMsg = m.messages.at(-1)
              const isSentenceComplete = /[.?!。？！]\s*$/.test((lastMsg?.original ?? '').trimEnd())
              const timeDiff = lastMsg
                ? Date.now() - new Date(lastMsg.timestamp).getTime()
                : Infinity
              const canMerge =
                lastMsg &&
                lastMsg.speaker === streamSpeaker &&
                (!isSentenceComplete || timeDiff < SILENCE_TIMEOUT_MS)
              if (canMerge && lastMsg) {
                // 기존 말풍선에 텍스트 append + alias 등록
                mergeAliasRef.current.set(msg.messageId, lastMsg.id)
                return {
                  ...m,
                  messages: m.messages.map((existing) =>
                    existing.id === lastMsg.id
                      ? {
                          ...existing,
                          original: existing.original + ' ' + (msg.originalText ?? ''),
                          streamPhase: 'stt' as const,
                        }
                      : existing
                  ),
                }
              }
              // 조건 불충족 — 새 말풍선 생성
              const newMsg: Message = {
                id: msg.messageId,
                speaker: streamSpeaker,
                original: msg.originalText ?? '',
                translation: carryoverTranslation,
                streamPhase: 'stt',
                timestamp: msg.timestamp,
              }
              return { ...m, messages: [...m.messages, newMsg] }
            })
          )
          scheduleTranslationTimeout(msg.messageId)
        } else if (msg.phase === 'translating') {
          // manual 번역 요청은 complete 모드여도 항상 스트리밍
          const isManual = msg.messageId.startsWith('__manual__')
          // complete 모드: manual이 아닌 경우 translating phase 무시
          if (settings.translationOutputMode === 'complete' && !isManual) return
          // pending 버블 번역 스트리밍
          if (msg.messageId === '__pending__') {
            setPendingTranscript((prev) =>
              prev ? { ...prev, translation: msg.partialTranslation ?? '' } : prev
            )
            return
          }
          // append 번역은 done에서만 처리 (스트리밍 생략)
          if (msg.messageId === '__pending_append__') return
          const resolvedId = isManual
            ? msg.messageId.slice('__manual__'.length)
            : (mergeAliasRef.current.get(msg.messageId) ?? msg.messageId)
          scheduleTranslationTimeout(resolvedId)
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === resolvedId
                        ? {
                            ...existing,
                            // 'me': EN 번역 결과 → original / others: KO 번역 결과 → translation
                            ...(existing.speaker === 'me'
                              ? { original: msg.partialTranslation ?? '' }
                              : { translation: msg.partialTranslation ?? '' }),
                            streamPhase: 'translating' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
        } else if (msg.phase === 'done') {
          // pending 버블 번역 완료
          if (msg.messageId === '__pending__') {
            setPendingTranscript((prev) =>
              prev ? { ...prev, translation: msg.translatedText ?? '' } : prev
            )
            return
          }
          // pending 버블 증분 번역 append
          if (msg.messageId === '__pending_append__') {
            const appended = msg.translatedText ?? ''
            if (appended) {
              setPendingTranscript((prev) =>
                prev
                  ? {
                      ...prev,
                      translation: prev.translation ? prev.translation + ' ' + appended : appended,
                    }
                  : prev
              )
            }
            return
          }
          // manual 번역 done: 실제 messageId 추출 후 단순 교체
          if (msg.messageId.startsWith('__manual__')) {
            const targetId = msg.messageId.slice('__manual__'.length)
            const t = translationTimeoutsRef.current.get(targetId)
            if (t) {
              clearTimeout(t)
              translationTimeoutsRef.current.delete(targetId)
            }
            setMeetings((prev) =>
              prev.map((m) =>
                m.id === activeMeetingId
                  ? {
                      ...m,
                      messages: m.messages.map((existing) =>
                        existing.id === targetId
                          ? existing.speaker === 'me'
                            ? {
                                ...existing,
                                original: msg.translatedText ?? '',
                                streamPhase: 'done' as const,
                              }
                            : {
                                ...existing,
                                original: msg.originalText ?? existing.original,
                                translation: msg.translatedText ?? '',
                                detectedLanguage: msg.detectedLanguage,
                                streamPhase: 'done' as const,
                              }
                          : existing
                      ),
                    }
                  : m
              )
            )
            return
          }
          // 타임아웃 해제 (alias된 경우 alias 기준으로 해제)
          const aliasedId = mergeAliasRef.current.get(msg.messageId)
          const resolvedDoneId = aliasedId ?? msg.messageId
          const isMerged = !!aliasedId
          if (isMerged) mergeAliasRef.current.delete(msg.messageId)
          const t = translationTimeoutsRef.current.get(resolvedDoneId)
          if (t) {
            clearTimeout(t)
            translationTimeoutsRef.current.delete(resolvedDoneId)
          }
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === resolvedDoneId
                        ? existing.speaker === 'me'
                          ? {
                              // 'me': EN 번역 결과 → original, KO 원본(translation) 유지
                              ...existing,
                              original: isMerged
                                ? (existing.original || '') + ' ' + (msg.translatedText ?? '')
                                : (msg.translatedText ?? ''),
                              streamPhase: 'done' as const,
                            }
                          : {
                              ...existing,
                              // 병합 시 원문은 stt phase에서 이미 append됨
                              original: isMerged
                                ? existing.original
                                : (msg.originalText ?? existing.original),
                              translation: isMerged
                                ? (existing.translation || '') + ' ' + (msg.translatedText ?? '')
                                : (msg.translatedText ?? ''),
                              detectedLanguage: msg.detectedLanguage,
                              streamPhase: 'done' as const,
                            }
                        : existing
                    ),
                  }
                : m
            )
          )
        }
      } else if (msg.type === 'tts_stream') {
        if (msg.phase === 'translating') {
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === msg.messageId
                        ? {
                            ...existing,
                            original: msg.partialText ?? '',
                            streamPhase: 'translating' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
        } else if (msg.phase === 'done') {
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === msg.messageId
                        ? {
                            ...existing,
                            original: msg.translatedText ?? '',
                            streamPhase: 'done' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
          setIsTtsPending(false)
          if (msg.audioData && settings.ttsAutoPlay) {
            playBase64Audio(msg.audioData, (audio) => {
              ttsAudioRef.current = audio
            })
              .then(() => {
                ttsAudioRef.current = null
              })
              .catch(() => {})
          }
        } else if (msg.phase === 'error') {
          setIsTtsPending(false)
        }
      } else if (msg.type === 'summary_stream') {
        if (msg.phase === 'delta') {
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId ? { ...m, summary: (m.summary ?? '') + (msg.text ?? '') } : m
            )
          )
        } else if (msg.phase === 'done') {
          if (isSummarizingTimeoutRef.current) {
            clearTimeout(isSummarizingTimeoutRef.current)
            isSummarizingTimeoutRef.current = null
          }
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId ? { ...m, summary: msg.summary ?? m.summary } : m
            )
          )
          setIsSummarizing(false)
        } else if (msg.phase === 'error') {
          if (isSummarizingTimeoutRef.current) {
            clearTimeout(isSummarizingTimeoutRef.current)
            isSummarizingTimeoutRef.current = null
          }
          setIsSummarizing(false)
        }
      }
    },
    [
      activeMeetingId,
      settings.silenceTimeout,
      settings.ttsAutoPlay,
      settings.translationTiming,
      settings.partialTranslationMode,
      settings.partialThrottleMs,
      settings.sourceLang,
      settings.targetLang,
      settings.translationModel,
      settings.translationOutputMode,
    ]
  )

  const {
    status: wsStatus,
    connect,
    disconnect,
    sendAudio,
    startRecording,
    stopRecording,
    sendSummarize,
    sendTranslate,
    sendTtsRequest,
  } = useWebSocket({
    meetingId: activeMeetingId,
    onMessage: handleWsMessage,
  })

  // sendTranslateRef 업데이트 (handleWsMessage 선언 이후에 useWebSocket이 오므로 ref로 전달)
  useEffect(() => {
    sendTranslateRef.current = sendTranslate
  }, [sendTranslate])

  // ─── Task #3: Audio capture ─────────────────────────────────────────────────

  const handleChunk = useCallback(
    (wav: string) => {
      sendAudio(
        wav,
        'speaker1',
        settings.sourceLang !== 'auto' ? settings.sourceLang : undefined,
        settings.targetLang,
        settings.translationModel
      )
    },
    [sendAudio, settings.sourceLang, settings.targetLang, settings.translationModel]
  )

  const {
    isRecording,
    audioLevel,
    error: audioError,
    start: startAudio,
    stop: stopAudio,
  } = useAudioCapture({
    onChunk: handleChunk,
    chunkDurationMs: settings.sttProvider === 'whisper' ? 2000 : 700,
    audioSource: settings.audioSource,
  })

  useEffect(() => {
    if (audioError) alert(audioError)
  }, [audioError])

  // WS 재연결 감지: 녹음 중에 연결이 끊겼다 재연결되면 startRecording 재전송
  const prevWsStatusRef = useRef<typeof wsStatus>('disconnected')
  useEffect(() => {
    const prev = prevWsStatusRef.current
    prevWsStatusRef.current = wsStatus
    if (wsStatus === 'connected' && prev !== 'connected' && isRecording) {
      console.warn('[WS] Reconnected during recording — resending startRecording')
      startRecording({
        sttProvider: settings.sttProvider,
        sourceLang: settings.sourceLang,
        targetLang: settings.targetLang,
        modelId: settings.translationModel,
        speaker: 'speaker1',
        translationTiming: settings.translationTiming,
      })
    }
  }, [
    wsStatus,
    isRecording,
    startRecording,
    settings.sttProvider,
    settings.sourceLang,
    settings.targetLang,
    settings.translationModel,
    settings.translationTiming,
  ])

  // STT 헬스 워치독: Transcribe 녹음 중 25초 무응답 시 startRecording 재전송 (백엔드 세션 자동 재시작 유도)
  useEffect(() => {
    if (!isRecording || settings.sttProvider !== 'transcribe' || wsStatus !== 'connected') return
    lastSttActivityTimeRef.current = Date.now()
    const id = setInterval(() => {
      if (Date.now() - lastSttActivityTimeRef.current > 25_000) {
        console.warn('[STT-Watchdog] 25s 무응답 — startRecording 재전송')
        startRecording({
          sttProvider: settings.sttProvider,
          sourceLang: settings.sourceLang,
          targetLang: settings.targetLang,
          modelId: settings.translationModel,
          speaker: 'speaker1',
          translationTiming: settings.translationTiming,
        })
        lastSttActivityTimeRef.current = Date.now()
      }
    }, 10_000)
    return () => clearInterval(id)
  }, [
    isRecording,
    settings.sttProvider,
    wsStatus,
    settings.sourceLang,
    settings.targetLang,
    settings.translationModel,
    settings.translationTiming,
    startRecording,
  ])

  // Connect on page load (and reconnect when active meeting changes); cleanup on unmount
  useEffect(() => {
    if (!HAS_API || !activeMeetingId) return
    connect()
  }, [activeMeetingId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggleRecording = useCallback(async () => {
    if (isRecording) {
      stopAudio()
      stopRecording()
      setPendingTranscript(null)
      if (partialThrottleRef.current) {
        clearTimeout(partialThrottleRef.current)
        partialThrottleRef.current = null
      }
      pendingPartialRef.current = ''
      translationTimeoutsRef.current.forEach((t) => clearTimeout(t))
      translationTimeoutsRef.current.clear()
      // 녹음 종료 시 자동 요약 (autoSummarizeMessageCount > 0 일 때)
      if (settings.autoSummarizeMessageCount > 0) {
        handleSummarizeRef.current()
      }
    } else {
      await startAudio()
      startRecording({
        sttProvider: settings.sttProvider,
        sourceLang: settings.sourceLang,
        targetLang: settings.targetLang,
        modelId: settings.translationModel,
        speaker: 'speaker1',
        translationTiming: settings.translationTiming,
      })
    }
  }, [
    isRecording,
    startAudio,
    stopAudio,
    startRecording,
    stopRecording,
    settings.sttProvider,
    settings.sourceLang,
    settings.targetLang,
    settings.translationModel,
    settings.translationTiming,
    settings.autoSummarizeMessageCount,
  ])

  // ─── Task #8: Summarize ─────────────────────────────────────────────────────

  // force=true: 버튼 클릭 시 stuck 상태 강제 리셋 후 재시도
  // force=false (기본): 자동 요약 트리거 — 이미 진행 중이면 skip
  const handleSummarize = useCallback(
    async (force = false) => {
      if (isSummarizingRef.current) {
        if (!force) return
        // 강제 리셋: 기존 safety timeout 취소 후 즉시 재시도
        if (isSummarizingTimeoutRef.current) {
          clearTimeout(isSummarizingTimeoutRef.current)
          isSummarizingTimeoutRef.current = null
        }
        isSummarizingRef.current = false
        setIsSummarizing(false)
      }

      if (!activeMeetingId) return

      // Read current messages count
      let msgCount = 0
      setMeetings((prev) => {
        msgCount = prev.find((m) => m.id === activeMeetingId)?.messages.length ?? 0
        return prev // no-op — just reading
      })

      if (msgCount === 0) return

      isSummarizingRef.current = true
      setIsSummarizing(true)

      if (!HAS_API) {
        setMeetings((prev) =>
          prev.map((m) =>
            m.id === activeMeetingId
              ? { ...m, summary: 'API 엔드포인트를 설정하면 Bedrock Claude로 요약이 생성됩니다.' }
              : m
          )
        )
        setSummaryOpen(true)
        isSummarizingRef.current = false
        setIsSummarizing(false)
        return
      }

      // WS 경로: connected이고 실제로 send 성공한 경우에만
      if (wsStatus === 'connected') {
        const sent = sendSummarize(activeMeetingId)
        if (sent) {
          setMeetings((prev) =>
            prev.map((m) => (m.id === activeMeetingId ? { ...m, summary: '' } : m))
          )
          setSummaryOpen(true)
          lastSummarizedCountRef.current[activeMeetingId] = msgCount
          // Safety: 60초 후 강제 리셋 (WS done/error 미수신 대비)
          if (isSummarizingTimeoutRef.current) clearTimeout(isSummarizingTimeoutRef.current)
          isSummarizingTimeoutRef.current = setTimeout(() => {
            isSummarizingRef.current = false
            setIsSummarizing(false)
          }, 60_000)
          return
        }
        // WS status는 connected이지만 실제 소켓 not OPEN → REST fallback
      }

      // REST API fallback
      try {
        const { summary } = await api.meetings.summarize(activeMeetingId)
        lastSummarizedCountRef.current[activeMeetingId] = msgCount
        setMeetings((prev) =>
          prev.map((m) => (m.id === activeMeetingId ? { ...m, summary: parseSummary(summary) } : m))
        )
        setSummaryOpen(true)
      } catch {
        // Silent — user can retry via button
      } finally {
        isSummarizingRef.current = false
        setIsSummarizing(false)
      }
    },
    [activeMeetingId, wsStatus, sendSummarize]
  )

  // Keep ref in sync for use inside timers
  useEffect(() => {
    handleSummarizeRef.current = handleSummarize
  })

  // Auto-summary: every N new messages (disabled when 0)
  useEffect(() => {
    const n = settings.autoSummarizeMessageCount
    if (n === 0) return
    const count = activeMeeting?.messages.length ?? 0
    const lastCount = lastSummarizedCountRef.current[activeMeetingId ?? ''] ?? 0
    if (count >= n && count - lastCount >= n) {
      handleSummarizeRef.current()
    }
  }, [activeMeeting?.messages.length, activeMeetingId, settings.autoSummarizeMessageCount])

  // ─── Task #9: TTS ────────────────────────────────────────────────────────────

  // ─── Unified audio stop ──────────────────────────────────────────────────────

  const handleStopAllAudio = useCallback(() => {
    if (ttsAudioRef.current) {
      ttsAudioRef.current.pause()
      ttsAudioRef.current = null
    }
    if (msgAudioRef.current) {
      msgAudioRef.current.pause()
      msgAudioRef.current = null
    }
    setIsTtsPending(false)
    setPlayingMessageId(null)
    setIsMessageLoading(false)
  }, [])

  // ─── Issue #44: Per-message manual translation ───────────────────────────────

  const handleTranslateMessage = useCallback(
    (id: string, text: string, speaker: string, detectedLanguage?: 'ko' | 'en') => {
      if (!text || wsStatus !== 'connected') return
      // 'me' speaker: KO → EN / others: detected/sourceLang → targetLang(KO)
      const isMe = speaker === 'me'
      const sourceLang = isMe
        ? 'ko'
        : (detectedLanguage ?? (settings.sourceLang !== 'auto' ? settings.sourceLang : 'en'))
      const targetLang = isMe ? 'en' : settings.targetLang
      // __manual__ 프리픽스: translating phase에서 complete 모드여도 항상 스트리밍
      sendTranslate(
        `__manual__${id}`,
        text,
        speaker,
        sourceLang,
        targetLang,
        settings.translationModel
      )
      // Optimistically mark the result field as loading
      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId
            ? {
                ...m,
                messages: m.messages.map((msg) =>
                  msg.id === id
                    ? isMe
                      ? { ...msg, original: '', streamPhase: 'stt' as const }
                      : { ...msg, translation: '', streamPhase: 'stt' as const }
                    : msg
                ),
              }
            : m
        )
      )
    },
    [
      wsStatus,
      sendTranslate,
      activeMeetingId,
      settings.sourceLang,
      settings.targetLang,
      settings.translationModel,
    ]
  )

  // ─── Per-message TTS ─────────────────────────────────────────────────────────

  const handlePlayMessage = useCallback(
    async (id: string, text: string) => {
      if (!HAS_API || !text) return

      // Stop everything before starting new playback
      if (ttsAudioRef.current) {
        ttsAudioRef.current.pause()
        ttsAudioRef.current = null
        setIsTtsPending(false)
      }
      if (msgAudioRef.current) {
        msgAudioRef.current.pause()
        msgAudioRef.current = null
      }

      setPlayingMessageId(id)
      setIsMessageLoading(true)

      try {
        const { audioData } = await api.tts.synthesize(
          text,
          settings.pollyEngine,
          settings.pollyVoiceId,
          false,
          settings.translationModel
        )
        setIsMessageLoading(false)
        await playBase64Audio(audioData, (audio) => {
          msgAudioRef.current = audio
        })
      } catch {
        // Silent on failure
      } finally {
        msgAudioRef.current = null
        setPlayingMessageId(null)
        setIsMessageLoading(false)
      }
    },
    [settings.pollyEngine, settings.pollyVoiceId, settings.translationModel]
  )

  const handleSend = useCallback(async () => {
    if (!ttsInput.trim() || isTtsPending) return
    const text = ttsInput.trim()
    setTtsInput('')

    if (!HAS_API) return

    // Stop any per-message playback before sending
    if (msgAudioRef.current) {
      msgAudioRef.current.pause()
      msgAudioRef.current = null
    }
    setPlayingMessageId(null)
    setIsMessageLoading(false)

    setIsTtsPending(true)

    // Optimistic: add 'me' bubble — original = EN translation (top), translation = KO input (bottom)
    const tempId = `tts-${Date.now()}`
    const optimistic: Message = {
      id: tempId,
      speaker: 'me',
      original: '',
      translation: text,
      timestamp: new Date().toISOString(),
      streamPhase: 'stt',
    }
    setMeetings((prev) =>
      prev.map((m) =>
        m.id === activeMeetingId ? { ...m, messages: [...m.messages, optimistic] } : m
      )
    )

    // WebSocket streaming path (preferred)
    if (wsStatus === 'connected') {
      sendTtsRequest(
        tempId,
        text,
        settings.translationModel,
        settings.pollyEngine,
        settings.pollyVoiceId
      )
      return
    }

    // Fallback: REST API when WebSocket is not connected
    try {
      const { audioData, translatedText } = await api.tts.synthesize(
        text,
        settings.pollyEngine,
        settings.pollyVoiceId,
        true,
        settings.translationModel
      )
      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId
            ? {
                ...m,
                messages: m.messages.map((msg) =>
                  msg.id === tempId
                    ? { ...msg, original: translatedText, streamPhase: 'done' as const }
                    : msg
                ),
              }
            : m
        )
      )
      if (settings.ttsAutoPlay) {
        await playBase64Audio(audioData, (audio) => {
          ttsAudioRef.current = audio
        })
        ttsAudioRef.current = null
      }
    } catch {
      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId
            ? { ...m, messages: m.messages.filter((msg) => msg.id !== tempId) }
            : m
        )
      )
    } finally {
      setIsTtsPending(false)
    }
  }, [
    ttsInput,
    isTtsPending,
    activeMeetingId,
    wsStatus,
    sendTtsRequest,
    settings.ttsAutoPlay,
    settings.pollyEngine,
    settings.pollyVoiceId,
    settings.translationModel,
  ])

  // ─── Meeting selection ────────────────────────────────────────────────────────

  const handleSelectMeeting = useCallback(
    (id: string) => {
      setActiveMeetingId(id)
      setSidebarOpen(false)
      if (HAS_API) loadMeetingMessages(id)
    },
    [loadMeetingMessages]
  )

  // ─── Render ──────────────────────────────────────────────────────────────────

  // Auth gate — show loading spinner while session is being restored
  if (HAS_COGNITO && authLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-slate-50 to-indigo-50 dark:from-[#070614] dark:to-[#0b0820]">
        <div className="flex items-center gap-2 text-slate-400 dark:text-slate-500 text-sm">
          <svg
            className="w-4 h-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        </div>
      </div>
    )
  }

  // Show login screen if Cognito is configured and user is not authenticated
  if (HAS_COGNITO && !user) {
    return <AuthScreen onAuth={setUser} />
  }

  if (isLoadingMeetings) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-slate-50 to-indigo-50 dark:from-[#070614] dark:to-[#0b0820]">
        <div className="flex items-center gap-2 text-slate-400 dark:text-slate-500 text-sm">
          <svg
            className="w-4 h-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
          <span>회의 목록 불러오는 중...</span>
        </div>
      </div>
    )
  }

  return (
    <main className="relative flex flex-col overflow-hidden font-sans" style={{ height: '100dvh' }}>
      {/* Background */}
      <div className="absolute inset-0 -z-10 bg-gradient-to-br from-slate-50 via-indigo-50/60 to-violet-50/80 dark:from-[#070614] dark:via-[#0b0820] dark:to-[#0f0828]" />
      <div className="orb-a absolute top-[8%] left-[3%] w-72 h-72 rounded-full bg-indigo-300/20 dark:bg-indigo-600/18 blur-3xl pointer-events-none -z-10" />
      <div className="orb-b absolute top-[35%] right-[5%] w-96 h-96 rounded-full bg-violet-300/15 dark:bg-violet-700/14 blur-3xl pointer-events-none -z-10" />

      <Header
        isRecording={isRecording}
        wsStatus={wsStatus}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        onToggleQuickTranslate={() => setQuickTranslateOpen((v) => !v)}
        quickTranslateOpen={quickTranslateOpen}
        onToggleSummary={() => setSummaryOpen((v) => !v)}
        summaryOpen={summaryOpen}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
        onLogout={HAS_COGNITO ? logout : undefined}
      />

      <div className="flex flex-1 overflow-hidden">
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/40 backdrop-blur-[2px] lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        <aside
          className={`
            fixed lg:relative top-12 lg:top-auto bottom-0 left-0
            z-40 lg:z-auto flex-shrink-0
            transition-transform duration-300 ease-in-out
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
          `}
        >
          <MeetingSidebar
            meetings={meetings}
            activeMeetingId={activeMeetingId}
            onSelect={handleSelectMeeting}
            onClose={() => setSidebarOpen(false)}
            onNewMeeting={handleNewMeeting}
            onDelete={handleDeleteMeeting}
            onGenerateTitle={handleGenerateTitle}
            generatingTitleId={generatingTitleId}
            isCreating={isCreatingMeeting}
          />
        </aside>

        <div className="flex-1 overflow-hidden min-w-0 flex flex-col">
          {/* 모바일 탭 바 */}
          <MobileTabBar
            activeTab={activeTab}
            onTabChange={(tab) => {
              setActiveTab(tab)
              if (tab === 'voice') setHasNewVoice(false)
              if (tab === 'notes') setHasNewNotes(false)
            }}
            hasNewVoice={hasNewVoice}
            hasNewNotes={hasNewNotes}
          />

          {/* 데스크톱: 좌우 분할 */}
          <div className="hidden lg:flex flex-1 overflow-hidden min-w-0">
            {/* 음성 입력 영역 */}
            <div className="flex-1 flex flex-col border-r border-slate-200/40 dark:border-white/6 min-w-0">
              <div className="px-3 py-1.5 border-b border-slate-200/40 dark:border-white/6 flex-shrink-0 flex items-center justify-between">
                <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 tracking-wide uppercase">
                  음성 입력
                </span>
                {/* 내 메모 접힌 상태: 펼치기 버튼 */}
                {notesCollapsed && (
                  <button
                    onClick={() => setNotesCollapsed(false)}
                    title="내 메모 펼치기"
                    className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium text-slate-400 dark:text-slate-500 hover:text-indigo-500 dark:hover:text-indigo-400 hover:bg-slate-100/60 dark:hover:bg-white/6 transition-colors"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="w-3 h-3"
                    >
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
                    </svg>
                    내 메모
                  </button>
                )}
              </div>
              <VoiceArea
                messages={activeMeeting?.messages ?? []}
                isRecording={isRecording}
                isProcessing={isTtsPending}
                playingMessageId={playingMessageId}
                isMessageLoading={isMessageLoading}
                onPlayMessage={handlePlayMessage}
                onStopMessage={handleStopAllAudio}
                onTranslateMessage={handleTranslateMessage}
                pendingTranscript={pendingTranscript}
              />
            </div>
            {/* 내 메모 영역 — 접기 가능 */}
            {!notesCollapsed && (
              <div className="w-80 flex-shrink-0 flex flex-col min-w-0">
                <div className="px-3 py-1.5 border-b border-slate-200/40 dark:border-white/6 flex-shrink-0 flex items-center justify-between">
                  <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 tracking-wide uppercase">
                    내 메모
                  </span>
                  <button
                    onClick={() => setNotesCollapsed(true)}
                    title="내 메모 접기"
                    className="w-5 h-5 flex items-center justify-center rounded text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100/60 dark:hover:bg-white/6 transition-colors"
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="w-3 h-3"
                    >
                      <polyline points="15 18 9 12 15 6" />
                    </svg>
                  </button>
                </div>
                <NotesArea
                  messages={activeMeeting?.messages ?? []}
                  playingMessageId={playingMessageId}
                  isMessageLoading={isMessageLoading}
                  onPlayMessage={handlePlayMessage}
                  onStopMessage={handleStopAllAudio}
                  onTranslateMessage={handleTranslateMessage}
                />
              </div>
            )}
          </div>

          {/* 모바일: 탭별 콘텐츠 */}
          <div className="flex lg:hidden flex-1 overflow-hidden min-w-0">
            {activeTab === 'voice' ? (
              <VoiceArea
                messages={activeMeeting?.messages ?? []}
                isRecording={isRecording}
                isProcessing={isTtsPending}
                playingMessageId={playingMessageId}
                isMessageLoading={isMessageLoading}
                onPlayMessage={handlePlayMessage}
                onStopMessage={handleStopAllAudio}
                onTranslateMessage={handleTranslateMessage}
                pendingTranscript={pendingTranscript}
              />
            ) : (
              <NotesArea
                messages={activeMeeting?.messages ?? []}
                playingMessageId={playingMessageId}
                isMessageLoading={isMessageLoading}
                onPlayMessage={handlePlayMessage}
                onStopMessage={handleStopAllAudio}
                onTranslateMessage={handleTranslateMessage}
              />
            )}
          </div>
        </div>

        {summaryOpen && (
          <div
            className="hidden sm:flex flex-shrink-0 relative"
            data-summary-width={summaryWidth}
            style={{ width: summaryWidth }}
          >
            {/* 리사이즈 핸들 */}
            <div
              onMouseDown={handleSummaryResize}
              className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize z-10 hover:bg-indigo-400/40 active:bg-indigo-500/50 transition-colors"
            />
            <SummaryPanel
              summary={activeMeeting?.summary}
              onClose={() => setSummaryOpen(false)}
              onSummarize={() => handleSummarize(true)}
              isSummarizing={isSummarizing}
            />
          </div>
        )}
      </div>

      {summaryOpen && (
        <div
          className="sm:hidden flex-shrink-0 border-t border-slate-200/60 dark:border-indigo-500/10"
          style={{ maxHeight: '45vh' }}
        >
          <SummaryPanel
            summary={activeMeeting?.summary}
            onClose={() => setSummaryOpen(false)}
            onSummarize={() => handleSummarize(true)}
            isSummarizing={isSummarizing}
          />
        </div>
      )}

      <ControlPanel
        isRecording={isRecording}
        onToggleRecording={handleToggleRecording}
        ttsInput={ttsInput}
        onTtsInputChange={setTtsInput}
        onSend={handleSend}
        onStopTts={handleStopAllAudio}
        audioLevel={audioLevel}
        isTtsPending={isTtsPending}
        activeTab={activeTab}
      />

      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          onUpdate={updateSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {quickTranslateOpen && (
        <QuickTranslatePopup settings={settings} onClose={() => setQuickTranslateOpen(false)} />
      )}
    </main>
  )
}
