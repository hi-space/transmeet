'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import Header from '@/components/Header'
import MeetingSidebar from '@/components/MeetingSidebar'
import ChatArea from '@/components/ChatArea'
import SummaryPanel from '@/components/SummaryPanel'
import ControlPanel from '@/components/ControlPanel'
import SettingsPanel from '@/components/SettingsPanel'
import AuthScreen from '@/components/AuthScreen'
import { Meeting, Message } from '@/types/meeting'
import { useAudioCapture } from '@/hooks/useAudioCapture'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSettings } from '@/hooks/useSettings'
import { useInterval } from '@/hooks/useInterval'
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

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Home() {
  const { user, isLoading: authLoading, setUser, logout } = useAuth()
  const { settings, updateSettings } = useSettings()

  const [meetings, setMeetings] = useState<Meeting[]>(HAS_API ? [] : MOCK_MEETINGS)
  const [activeMeetingId, setActiveMeetingId] = useState(HAS_API ? '' : MOCK_MEETINGS[0].id)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [ttsInput, setTtsInput] = useState('')
  const [isSummarizing, setIsSummarizing] = useState(false)
  const [isTtsPending, setIsTtsPending] = useState(false)
  const [pendingTranscript, setPendingTranscript] = useState<{
    messageId: string
    text: string
    speaker: string
  } | null>(null)
  const [isLoadingMeetings, setIsLoadingMeetings] = useState(HAS_API)
  const [isCreatingMeeting, setIsCreatingMeeting] = useState(false)
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null)
  const [isMessageLoading, setIsMessageLoading] = useState(false)

  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const msgAudioRef = useRef<HTMLAudioElement | null>(null)
  // Maps stream messageId -> displayed message id (for consecutive-message merging)
  const mergeMapRef = useRef<Map<string, string>>(new Map())
  // Tracks displayed message IDs that have been merged into (protects their original text)
  const mergedMsgIdsRef = useRef<Set<string>>(new Set())

  // Track last auto-summarized message count per meeting
  const lastSummarizedCountRef = useRef<Record<string, number>>({})
  // Stable ref for handleSummarize to avoid stale closure in timers
  const handleSummarizeRef = useRef<() => Promise<void>>(() => Promise.resolve())
  // Safety: reset isSummarizing if WS done/error event is never received
  const isSummarizingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const activeMeeting = meetings.find((m) => m.id === activeMeetingId) ?? meetings[0]

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
        const MERGE_WINDOW_MS = 3000
        if (msg.phase === 'stt_partial') {
          // Word-by-word Transcribe partial — show in pending bubble, not committed messages
          setPendingTranscript({
            messageId: msg.messageId,
            text: msg.originalText ?? '',
            speaker: msg.speaker ?? 'speaker1',
          })
          return
        }
        if (msg.phase === 'stt') {
          // Final sentence from Transcribe — clear pending bubble (partial ID differs from final ID)
          setPendingTranscript(null)
          const streamSpeaker = (msg.speaker as Message['speaker']) ?? 'speaker1'
          setMeetings((prev) => {
            const meeting = prev.find((m) => m.id === activeMeetingId)
            const messages = meeting?.messages ?? []
            const lastMsg = messages[messages.length - 1]
            const shouldMerge =
              lastMsg &&
              lastMsg.speaker === streamSpeaker &&
              Date.now() - new Date(lastMsg.timestamp).getTime() < MERGE_WINDOW_MS &&
              lastMsg.streamPhase !== undefined
            if (shouldMerge) {
              // Reuse existing bubble; track messageId -> displayed id
              mergeMapRef.current.set(msg.messageId, lastMsg.id)
              mergedMsgIdsRef.current.add(lastMsg.id)
              return prev.map((m) =>
                m.id === activeMeetingId
                  ? {
                      ...m,
                      messages: m.messages.map((existing) =>
                        existing.id === lastMsg.id
                          ? {
                              ...existing,
                              original: existing.original + ' ' + (msg.originalText ?? ''),
                              translation: '',
                              streamPhase: 'stt' as const,
                            }
                          : existing
                      ),
                    }
                  : m
              )
            }
            // New bubble
            const newMsg: Message = {
              id: msg.messageId,
              speaker: streamSpeaker,
              original: msg.originalText ?? '',
              translation: '',
              streamPhase: 'stt',
              timestamp: msg.timestamp,
            }
            return prev.map((m) =>
              m.id === activeMeetingId ? { ...m, messages: [...m.messages, newMsg] } : m
            )
          })
        } else if (msg.phase === 'translating') {
          const resolvedId = mergeMapRef.current.get(msg.messageId) ?? msg.messageId
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === resolvedId
                        ? {
                            ...existing,
                            translation: msg.partialTranslation ?? '',
                            streamPhase: 'translating' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
        } else if (msg.phase === 'done') {
          const resolvedId = mergeMapRef.current.get(msg.messageId) ?? msg.messageId
          const isMerged = mergeMapRef.current.has(msg.messageId)
          const isProtected = mergedMsgIdsRef.current.has(resolvedId)
          mergeMapRef.current.delete(msg.messageId)
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === resolvedId
                        ? {
                            ...existing,
                            // Preserve original if this bubble was merged into or is a merge target
                            original:
                              isMerged || isProtected
                                ? existing.original
                                : (msg.originalText ?? existing.original),
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
    [activeMeetingId]
  )

  const {
    status: wsStatus,
    connect,
    disconnect,
    sendAudio,
    startRecording,
    stopRecording,
    sendSummarize,
  } = useWebSocket({
    meetingId: activeMeetingId,
    onMessage: handleWsMessage,
  })

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
  })

  useEffect(() => {
    if (audioError) alert(audioError)
  }, [audioError])

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
      mergeMapRef.current.clear()
      mergedMsgIdsRef.current.clear()
    } else {
      await startAudio()
      startRecording({
        sttProvider: settings.sttProvider,
        sourceLang: settings.sourceLang,
        targetLang: settings.targetLang,
        modelId: settings.translationModel,
        speaker: 'speaker1',
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
  ])

  // ─── Task #8: Summarize ─────────────────────────────────────────────────────

  const handleSummarize = useCallback(async () => {
    console.log('[auto-summary] handleSummarize called', {
      isSummarizing,
      activeMeetingId,
      wsStatus,
    })

    if (isSummarizing || !activeMeetingId) {
      console.log(
        '[auto-summary] skipped: isSummarizing=',
        isSummarizing,
        '/ activeMeetingId=',
        activeMeetingId
      )
      return
    }

    // Read current messages count from state via functional setter trick
    let msgCount = 0
    setMeetings((prev) => {
      msgCount = prev.find((m) => m.id === activeMeetingId)?.messages.length ?? 0
      return prev // no-op — just reading
    })

    console.log('[auto-summary] msgCount=', msgCount)
    if (msgCount === 0) {
      console.log('[auto-summary] skipped: no messages')
      return
    }
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
      setIsSummarizing(false)
      return
    }

    if (wsStatus === 'connected') {
      // Stream summary via WebSocket — isSummarizing cleared on 'done'/'error'
      console.log('[auto-summary] sending via WebSocket')
      setMeetings((prev) => prev.map((m) => (m.id === activeMeetingId ? { ...m, summary: '' } : m)))
      setSummaryOpen(true)
      sendSummarize(activeMeetingId)
      // Update ref so "every 10 messages" trigger doesn't re-fire immediately
      lastSummarizedCountRef.current[activeMeetingId] = msgCount
      // Safety: reset isSummarizing after 90s in case WS done/error event is lost
      if (isSummarizingTimeoutRef.current) clearTimeout(isSummarizingTimeoutRef.current)
      isSummarizingTimeoutRef.current = setTimeout(() => {
        console.warn('[auto-summary] isSummarizing safety reset after 90s timeout')
        setIsSummarizing(false)
      }, 90_000)
      return
    }

    // Fallback: REST API (WebSocket not connected)
    console.log('[auto-summary] sending via REST API')
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
      setIsSummarizing(false)
    }
  }, [isSummarizing, activeMeetingId, wsStatus, sendSummarize])

  // Keep ref in sync for use inside timers
  useEffect(() => {
    handleSummarizeRef.current = handleSummarize
  })

  // Auto-summary: every 10 new messages
  useEffect(() => {
    const count = activeMeeting?.messages.length ?? 0
    const lastCount = lastSummarizedCountRef.current[activeMeetingId ?? ''] ?? 0
    if (count >= 10 && count - lastCount >= 10) {
      handleSummarizeRef.current()
    }
  }, [activeMeeting?.messages.length, activeMeetingId])

  // Auto-summary: configurable interval while recording (0 = disabled)
  const autoSummarizeMs =
    isRecording && settings.autoSummarizeInterval > 0
      ? settings.autoSummarizeInterval * 60 * 1000
      : null
  useInterval(() => {
    console.log('[auto-summary] interval tick', {
      isRecording,
      autoSummarizeInterval: settings.autoSummarizeInterval,
      autoSummarizeMs,
      isSummarizing,
      msgCount: activeMeeting?.messages.length,
    })
    handleSummarizeRef.current()
  }, autoSummarizeMs)

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
          false
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
    [settings.pollyEngine, settings.pollyVoiceId]
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

    // Optimistic: add message with placeholder translation
    const tempId = `tts-${Date.now()}`
    const optimistic: Message = {
      id: tempId,
      speaker: 'me',
      original: '번역 중...',
      translation: text,
      timestamp: new Date().toISOString(),
    }
    setMeetings((prev) =>
      prev.map((m) =>
        m.id === activeMeetingId ? { ...m, messages: [...m.messages, optimistic] } : m
      )
    )

    try {
      const { audioData, translatedText } = await api.tts.synthesize(
        text,
        settings.pollyEngine,
        settings.pollyVoiceId
      )

      // Replace placeholder with real translation
      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId
            ? {
                ...m,
                messages: m.messages.map((msg) =>
                  msg.id === tempId ? { ...msg, original: translatedText } : msg
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
      // Remove optimistic on failure
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
    settings.ttsAutoPlay,
    settings.pollyEngine,
    settings.pollyVoiceId,
  ])

  // ─── Meeting selection ────────────────────────────────────────────────────────

  const handleSelectMeeting = useCallback(
    (id: string) => {
      mergeMapRef.current.clear()
      mergedMsgIdsRef.current.clear()
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
          <ChatArea
            messages={activeMeeting?.messages ?? []}
            isRecording={isRecording}
            isProcessing={isTtsPending}
            playingMessageId={playingMessageId}
            isMessageLoading={isMessageLoading}
            onPlayMessage={handlePlayMessage}
            onStopMessage={handleStopAllAudio}
            pendingTranscript={pendingTranscript}
          />
        </div>

        {summaryOpen && (
          <div className="hidden sm:flex w-64 flex-shrink-0">
            <SummaryPanel
              summary={activeMeeting?.summary}
              onClose={() => setSummaryOpen(false)}
              onSummarize={handleSummarize}
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
            onSummarize={handleSummarize}
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
      />

      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          onUpdate={updateSettings}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </main>
  )
}
