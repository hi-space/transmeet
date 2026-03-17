'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import Header from '@/components/Header'
import MeetingSidebar from '@/components/MeetingSidebar'
import ChatArea from '@/components/ChatArea'
import SummaryPanel from '@/components/SummaryPanel'
import ControlPanel from '@/components/ControlPanel'
import { Meeting, Message } from '@/types/meeting'
import { useAudioCapture } from '@/hooks/useAudioCapture'
import { useWebSocket } from '@/hooks/useWebSocket'
import type { WsServerMessage } from '@/lib/websocket'
import { api, toMeeting, parseSummary } from '@/lib/api'

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
    summary: ['Q1 전환율 12% 증가', '온보딩 재설계가 주요 성과 요인', 'Q2 목표: 전환율 최적화'],
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

function playBase64Audio(base64: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const binary = atob(base64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    const blob = new Blob([bytes], { type: 'audio/mp3' })
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
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
  const [meetings, setMeetings] = useState<Meeting[]>(HAS_API ? [] : MOCK_MEETINGS)
  const [activeMeetingId, setActiveMeetingId] = useState(HAS_API ? '' : MOCK_MEETINGS[0].id)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [ttsInput, setTtsInput] = useState('')
  const [isSummarizing, setIsSummarizing] = useState(false)
  const [isTtsPending, setIsTtsPending] = useState(false)
  const [isLoadingMeetings, setIsLoadingMeetings] = useState(HAS_API)
  const [isCreatingMeeting, setIsCreatingMeeting] = useState(false)

  // Track last auto-summarized message count per meeting
  const lastSummarizedCountRef = useRef<Record<string, number>>({})
  // Stable ref for handleSummarize to avoid stale closure in timers
  const handleSummarizeRef = useRef<() => Promise<void>>(() => Promise.resolve())

  const activeMeeting = meetings.find((m) => m.id === activeMeetingId) ?? meetings[0]

  // ─── Task #10: Load meetings on mount ──────────────────────────────────────

  useEffect(() => {
    if (!HAS_API) return

    api.meetings
      .list()
      .then((list) => {
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

  // ─── Task #5: WebSocket subtitle handler ───────────────────────────────────

  const handleWsMessage = useCallback(
    (msg: WsServerMessage) => {
      if (msg.type !== 'subtitle') return

      const newMsg: Message = {
        id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        speaker: (msg.speaker as Message['speaker']) ?? 'speaker1',
        original: msg.originalText,
        translation: msg.translatedText,
        timestamp: msg.timestamp,
      }

      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId ? { ...m, messages: [...m.messages, newMsg] } : m
        )
      )
    },
    [activeMeetingId]
  )

  const {
    status: wsStatus,
    connect,
    disconnect,
    sendAudio,
  } = useWebSocket({
    meetingId: activeMeetingId,
    onMessage: handleWsMessage,
  })

  // ─── Task #3: Audio capture ─────────────────────────────────────────────────

  const handleChunk = useCallback(
    (wav: string) => {
      sendAudio(wav, 'speaker1')
    },
    [sendAudio]
  )

  const {
    isRecording,
    audioLevel,
    error: audioError,
    start: startAudio,
    stop: stopAudio,
  } = useAudioCapture({ onChunk: handleChunk })

  useEffect(() => {
    if (audioError) alert(audioError)
  }, [audioError])

  const handleToggleRecording = useCallback(async () => {
    if (isRecording) {
      stopAudio()
      disconnect()
    } else {
      connect()
      await startAudio()
    }
  }, [isRecording, startAudio, stopAudio, connect, disconnect])

  // ─── Task #8: Summarize ─────────────────────────────────────────────────────

  const handleSummarize = useCallback(async () => {
    if (isSummarizing || !activeMeetingId) return

    // Read current messages count from state via functional setter trick
    let msgCount = 0
    setMeetings((prev) => {
      msgCount = prev.find((m) => m.id === activeMeetingId)?.messages.length ?? 0
      return prev // no-op — just reading
    })

    if (msgCount === 0) return
    setIsSummarizing(true)

    if (!HAS_API) {
      setMeetings((prev) =>
        prev.map((m) =>
          m.id === activeMeetingId
            ? {
                ...m,
                summary: ['API 엔드포인트를 설정하면 Bedrock Claude로 요약이 생성됩니다.'],
              }
            : m
        )
      )
      setSummaryOpen(true)
      setIsSummarizing(false)
      return
    }

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
  }, [isSummarizing, activeMeetingId])

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

  // Auto-summary: every 5 minutes while recording
  useEffect(() => {
    if (!isRecording) return
    const timer = setInterval(
      () => {
        handleSummarizeRef.current()
      },
      5 * 60 * 1000
    )
    return () => clearInterval(timer)
  }, [isRecording])

  // ─── Task #9: TTS ────────────────────────────────────────────────────────────

  const handleSend = useCallback(async () => {
    if (!ttsInput.trim() || isTtsPending) return
    const text = ttsInput.trim()
    setTtsInput('')

    if (!HAS_API) return

    setIsTtsPending(true)

    // Optimistic: add message with placeholder English text
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
      const { audioData, translatedText } = await api.tts.synthesize(text)

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

      await playBase64Audio(audioData)
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
  }, [ttsInput, isTtsPending, activeMeetingId])

  // ─── Meeting selection ────────────────────────────────────────────────────────

  const handleSelectMeeting = (id: string) => {
    setActiveMeetingId(id)
    setSidebarOpen(false)
  }

  // ─── Render ──────────────────────────────────────────────────────────────────

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
    <main className="relative flex flex-col h-screen overflow-hidden font-sans">
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
            isCreating={isCreatingMeeting}
          />
        </aside>

        <div className="flex-1 overflow-hidden min-w-0 flex flex-col">
          <ChatArea
            messages={activeMeeting?.messages ?? []}
            isRecording={isRecording}
            isProcessing={isTtsPending}
          />
        </div>

        {summaryOpen && (
          <div className="hidden sm:flex w-64 flex-shrink-0">
            <SummaryPanel
              summary={activeMeeting?.summary ?? []}
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
            summary={activeMeeting?.summary ?? []}
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
        audioLevel={audioLevel}
        isTtsPending={isTtsPending}
      />
    </main>
  )
}
