'use client'

import { useState, useCallback, useEffect, useRef, type CSSProperties } from 'react'
import Header, { LanguageDirection } from '@/components/Header'
import MeetingSidebar from '@/components/MeetingSidebar'
import SummaryPanel from '@/components/SummaryPanel'
import ControlPanel from '@/components/ControlPanel'
import SettingsPanel from '@/components/SettingsPanel'
import QuickTranslatePopup from '@/components/QuickTranslatePopup'
import AuthScreen from '@/components/AuthScreen'
import MobileTabBar from '@/components/MobileTabBar'
import MeetingPagePanel, { PAGE_GEN_IDLE, type PageGenState } from '@/components/MeetingPagePanel'
import NotesArea from '@/components/NotesArea'
import NotesComposer from '@/components/NotesComposer'
import VoiceArea, { VoiceFontSizeControl } from '@/components/VoiceArea'
import MeetingContentStatus from '@/components/MeetingContentStatus'
import EditableTitle from '@/components/EditableTitle'
import Icon from '@/components/ui/Icon'
import Tabs from '@/components/ui/Tabs'
import Dialog from '@/components/ui/Dialog'
import MeetingActions, {
  isEnding,
  MeetingStatus,
  type AudioPrepState,
  type MeetingEndState,
} from '@/components/MeetingActions'
import { Meeting, Message, type MeetingBrief } from '@/types/meeting'
import { useAudioCapture } from '@/hooks/useAudioCapture'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSettings, type Settings } from '@/hooks/useSettings'
import { useSummaryResize } from '@/hooks/useSummaryResize'
import { useMeetingBrief } from '@/hooks/useMeetingBrief'
import { useAuth } from '@/context/AuthContext'
import type { WsServerMessage } from '@/lib/websocket'
import { api, toMeeting, parseSummary } from '@/lib/api'
import { getVisibleUtilityTab } from '@/lib/workspace'
import {
  sttChunkDuration,
  sttRecordingAction,
  isManagedStt,
  shouldStopSttRecording,
  type SttProvider,
  type SttStatus,
} from '@/lib/sttProviders'
import SttStatusPanel from '@/components/SttStatusPanel'

const HAS_COGNITO = !!process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID

// ─── Dev fallback when API is not configured ────────────────────────────────

const HAS_API = !!process.env.NEXT_PUBLIC_API_ENDPOINT

// 미팅 페이지 생성: 하네스 제한(15분) + 여유 1분 뒤에도 소식이 없으면 오류로 본다
const PAGE_GEN_TIMEOUT_MS = 31 * 60 * 1000 // 하네스 TimeoutSeconds(1800) + 여유
const PAGE_GEN_TIMEOUT_MESSAGE = '응답이 없습니다. 미팅을 다시 열어 확인하세요'
const PAGE_GEN_POLL_MS = 20 * 1000
const PAGE_GEN_CLOCK_SKEW_MS = 5 * 60 * 1000

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

function countSentences(text: string): number {
  const matches = text.match(/[.?!。？！]/g)
  return matches ? matches.length : 0
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

// ─── 번역 방향 ────────────────────────────────────────────────────────────────

/**
 * 번역 방향을 결정한다.
 *
 * sourceLang 이 'auto' 면 방향이 고정 설정이 아니라 발화마다 감지된 언어에서
 * 나온다 — 감지 언어의 반대편이 타겟이다. 감지값이 아직 없으면(첫 partial 등)
 * 'en' 으로 가정한다.
 *
 * 백엔드도 같은 규칙을 독립적으로 적용한다 (ws_handler._auto_target_lang).
 * 프론트가 계산하는 이유는 partial 번역 요청에 방향을 실어 보내야 하기 때문.
 */
function resolveDirection(
  sourceLang: Settings['sourceLang'],
  targetLang: Settings['targetLang'],
  detected?: 'ko' | 'en' | null
): { source: 'ko' | 'en'; target: 'ko' | 'en' } {
  if (sourceLang === 'auto') {
    const source = detected ?? 'en'
    return { source, target: source === 'ko' ? 'en' : 'ko' }
  }
  return { source: sourceLang, target: targetLang }
}

// ─── 빈 회의 정리 ─────────────────────────────────────────────────────────────

/**
 * 내용이 하나도 없는 회의인지 판단 — 진입 시 자동 정리 대상.
 * messageCount 는 목록 API 가 내려주는 값. 과거 레코드에는 없을 수 있으므로
 * undefined 는 "알 수 없음"으로 보고 절대 삭제하지 않는다 (메시지 있는 회의 유실 방지).
 */
function isPrunable(m: Meeting): boolean {
  if (m.summary || m.hasAudio) return false
  return m.messageCount === 0
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Home() {
  const { user, isLoading: authLoading, setUser, logout } = useAuth()
  const { settings, updateSettings } = useSettings()
  const [sttStatuses, setSttStatuses] = useState<Partial<Record<SttProvider, SttStatus>>>({})
  const selectedSttStatus = sttStatuses[settings.sttProvider]
  const {
    width: summaryWidth,
    minWidth: summaryMinWidth,
    maxWidth: summaryMaxWidth,
    handlePointerDown: handleSummaryResize,
    handleKeyDown: handleResizeKeyDown,
  } = useSummaryResize(360)

  const [meetings, setMeetings] = useState<Meeting[]>(HAS_API ? [] : MOCK_MEETINGS)
  const [activeMeetingId, setActiveMeetingId] = useState(HAS_API ? '' : MOCK_MEETINGS[0].id)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [desktopLayout, setDesktopLayout] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickTranslateOpen, setQuickTranslateOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'voice' | 'notes' | 'summary' | 'page'>('voice')
  const [rightPanel, setRightPanel] = useState<'notes' | 'summary' | 'page'>('summary')
  // pageGen 은 현재 미팅의 생성 상태, 원본은 미팅별 Map — 다른 미팅을 보다 돌아와도 "생성 중"이 이어진다
  const [pageGen, setPageGen] = useState<PageGenState>(PAGE_GEN_IDLE)
  const pageGenByMeetingRef = useRef<Map<string, PageGenState>>(new Map())
  const pageGenTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  const [hasNewPage, setHasNewPage] = useState(false)
  // meetingId 가 없는 (구버전 백엔드) page_stream 이벤트는 마지막으로 요청한 미팅에 귀속한다
  const pageGenMeetingIdRef = useRef<string | null>(null)
  // 공유 해제는 생성과 별도 상태 — 실패해도 생성 오류 화면을 덮지 않는다
  const [unpublishState, setUnpublishState] = useState<{
    meetingId: string
    status: 'pending' | 'error'
    error?: string
  } | null>(null)
  const unpublishingRef = useRef<Set<string>>(new Set())
  const [hasNewVoice, setHasNewVoice] = useState(false)
  // 미팅 종료(오디오 → 요약 → 페이지 생성·메일) 진행과 녹음 파일 준비 상태
  const [meetingEnd, setMeetingEnd] = useState<MeetingEndState | null>(null)
  const [audioPrep, setAudioPrep] = useState<AudioPrepState | null>(null)
  const [hasNewNotes, setHasNewNotes] = useState(false)
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
  const [meetingLoadStates, setMeetingLoadStates] = useState<
    Record<string, 'loading' | 'error' | undefined>
  >({})
  const loadingMeetingIdsRef = useRef(new Set<string>())
  const [meetingListError, setMeetingListError] = useState<string | null>(null)
  const [isCreatingMeeting, setIsCreatingMeeting] = useState(HAS_API)
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null)
  const [isMessageLoading, setIsMessageLoading] = useState(false)
  const [quickTranslateStream, setQuickTranslateStream] = useState<{
    text: string
    phase: 'translating' | 'done'
  } | null>(null)

  const notesComposerRef = useRef<HTMLFormElement>(null)
  const pageFooterRef = useRef<HTMLDivElement>(null)
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const msgAudioRef = useRef<HTMLAudioElement | null>(null)
  const meetingsRef = useRef(meetings)
  meetingsRef.current = meetings
  const activeMeetingIdRef = useRef(activeMeetingId)
  activeMeetingIdRef.current = activeMeetingId

  const setPageGenFor = useCallback(
    (meetingId: string, next: PageGenState | ((prev: PageGenState) => PageGenState)) => {
      const map = pageGenByMeetingRef.current
      const prev = map.get(meetingId) ?? PAGE_GEN_IDLE
      const value = typeof next === 'function' ? next(prev) : next
      if (value.status === 'idle') map.delete(meetingId)
      else map.set(meetingId, value)
      if (value.status !== 'generating') {
        const timer = pageGenTimersRef.current.get(meetingId)
        if (timer) clearTimeout(timer)
        pageGenTimersRef.current.delete(meetingId)
      }
      if (meetingId === activeMeetingIdRef.current) setPageGen(value)
    },
    []
  )

  // 수동 요청과 서버의 자동 생성이 같은 타임아웃·재연결 확인 경로를 쓴다.
  const startPageGenFor = useCallback(
    (meetingId: string) => {
      if (pageGenByMeetingRef.current.get(meetingId)?.status === 'generating') return
      pageGenMeetingIdRef.current = meetingId
      setUnpublishState((s) => (s?.meetingId === meetingId ? null : s))
      const startedAt = Date.now()
      setPageGenFor(meetingId, {
        status: 'generating',
        log: [],
        startedAt,
        prevPageId: meetingsRef.current.find((m) => m.id === meetingId)?.page?.pageId,
      })
      // 하네스 제한(30분)을 넘겨도 아무 이벤트가 없으면 오류로 바꾼다.
      const prevTimer = pageGenTimersRef.current.get(meetingId)
      if (prevTimer) clearTimeout(prevTimer)
      pageGenTimersRef.current.set(
        meetingId,
        setTimeout(() => {
          setPageGenFor(meetingId, (g) =>
            g.status === 'generating' && g.startedAt === startedAt
              ? { ...g, status: 'error', error: PAGE_GEN_TIMEOUT_MESSAGE }
              : g
          )
        }, PAGE_GEN_TIMEOUT_MS)
      )
    },
    [setPageGenFor]
  )
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
  // AUTO 모드: 마지막으로 감지된 언어. ref 는 handleWsMessage 안에서 즉시 비교하려고,
  // state 는 헤더 배지 렌더링용 — 둘을 함께 갱신한다.
  const detectedLangRef = useRef<'ko' | 'en' | null>(null)
  const [detectedLang, setDetectedLang] = useState<'ko' | 'en' | null>(null)
  // 같은 서버 에러가 반복될 때 alert 이 도배되지 않도록 마지막 메시지를 기억한다
  const lastWsErrorRef = useRef<string | null>(null)
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
  const sendQaRequestRef = useRef<
    (
      messageId: string,
      question: string,
      messages?: { speaker: string; original: string }[],
      modelId?: string
    ) => boolean
  >(() => false)
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
  // 마운트 초기화(새 회의 생성)를 한 번만 실행하기 위한 가드
  const didInitRef = useRef(false)
  // 초기 요청이 끝나기 전에 사용하거나 삭제한 회의를 오래된 목록 응답으로 되돌리지 않는다.
  const openedMeetingIdsRef = useRef(new Set<string>())
  const deletedMeetingIdsRef = useRef(new Set<string>())

  const activeMeeting = meetings.find((m) => m.id === activeMeetingId)
  const activeMeetingLoadState = meetingLoadStates[activeMeetingId]
  const handleBriefUpdate = useCallback((meetingId: string, brief: MeetingBrief) => {
    setMeetings((prev) =>
      prev.map((meeting) =>
        meeting.id === meetingId &&
        (!meeting.briefUpdatedAt || brief.briefUpdatedAt >= meeting.briefUpdatedAt)
          ? { ...meeting, ...brief }
          : meeting
      )
    )
  }, [])
  const isBriefRefreshing = useMeetingBrief(
    activeMeeting,
    HAS_API && !activeMeetingLoadState,
    handleBriefUpdate
  )

  // 탭 배지: 비활성 탭에 새 메시지 도착 시 표시
  useEffect(() => {
    const msgs = activeMeeting?.messages ?? []
    if (msgs.length === 0) return
    const lastMsg = msgs[msgs.length - 1]
    if (lastMsg.speaker !== 'me' && activeTab !== 'voice') setHasNewVoice(true)
    if (lastMsg.speaker === 'me' && activeTab !== 'notes') setHasNewNotes(true)
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
    // 회의를 빠르게 오가도 진행 중인 요청은 재사용하고, 상태는 회의별로 유지한다.
    if (loadingMeetingIdsRef.current.has(id)) return
    loadingMeetingIdsRef.current.add(id)
    openedMeetingIdsRef.current.add(id)
    setMeetingLoadStates((prev) => ({ ...prev, [id]: 'loading' }))
    try {
      const full = await api.meetings.get(id)
      const meeting = toMeeting(full)
      // 이미 요약이 있다면 현재 메시지 수를 마지막 요약 시점으로 간주 —
      // 로드 자체가 자동 요약을 재실행하지 않도록 한다.
      if (meeting.summary) {
        lastSummarizedCountRef.current[id] = meeting.messages.length
      }
      setMeetings((prev) =>
        prev.map((current) => {
          if (current.id !== id) return current
          // A brief may finish while this older meeting snapshot is in flight.
          if (
            current.briefUpdatedAt &&
            (!meeting.briefUpdatedAt || current.briefUpdatedAt > meeting.briefUpdatedAt)
          ) {
            return toMeeting({
              ...full,
              brief: current.brief,
              briefUpdatedAt: current.briefUpdatedAt,
              briefMessageCount: current.briefMessageCount,
              briefLastMessageId: current.briefLastMessageId,
            })
          }
          return meeting
        })
      )
      setMeetingLoadStates((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    } catch {
      setMeetingLoadStates((prev) => ({ ...prev, [id]: 'error' }))
    } finally {
      loadingMeetingIdsRef.current.delete(id)
    }
  }, [])

  useEffect(() => {
    if (!HAS_API) return
    // 목록은 사용자별이다 — 인증을 마친 뒤에 한 번 불러온다.
    if (HAS_COGNITO && !user) return
    // StrictMode 는 개발 중 effect 를 두 번 실행한다 — 회의가 두 개 생기지 않도록 막는다
    if (didInitRef.current) return
    didInitRef.current = true

    async function init() {
      const linked = new URLSearchParams(window.location.search).get('meeting')
      if (linked) window.history.replaceState(null, '', window.location.pathname)

      // 목록과 새 회의 생성은 서로 기다리지 않는다. 도착한 결과부터 화면에 반영한다.
      const listRequest = api.meetings
        .list()
        .then((items) => {
          const list = items.map(toMeeting).filter((m) => !deletedMeetingIdsRef.current.has(m.id))
          setMeetings((prev) => {
            const existingIds = new Set(prev.map((m) => m.id))
            return [...prev, ...list.filter((m) => !existingIds.has(m.id))]
          })
          if (linked && list.some((m) => m.id === linked)) {
            setActiveMeetingId((current) => current || linked)
            loadMeetingMessages(linked)
          }
          return list
        })
        .catch((error: unknown) => {
          setMeetingListError('회의 목록을 불러오지 못했습니다. 새로고침해 다시 시도해 주세요.')
          throw error
        })
        .finally(() => setIsLoadingMeetings(false))

      // 이전 회의에 이어 붙지 않도록 새 회의를 준비하되, 사용자가 고른 회의는 유지한다.
      const createRequest = api.meetings
        .create()
        .then((item) => {
          const created = toMeeting(item)
          if (deletedMeetingIdsRef.current.has(created.id)) return created
          setMeetings((prev) => [
            prev.find((m) => m.id === created.id) ?? created,
            ...prev.filter((m) => m.id !== created.id),
          ])
          if (!linked) setActiveMeetingId((current) => current || created.id)
          return created
        })
        .finally(() => setIsCreatingMeeting(false))

      const [listed, creation] = await Promise.allSettled([listRequest, createRequest])
      const list =
        listed.status === 'fulfilled'
          ? listed.value.filter((m) => !deletedMeetingIdsRef.current.has(m.id))
          : []
      if (creation.status === 'rejected') {
        // 생성 실패 시 기존 회의를 열고 정리는 건너뛴다.
        const fallback = list.find((m) => m.id === linked) ?? list[0]
        if (fallback && !activeMeetingIdRef.current) {
          setActiveMeetingId((current) => current || fallback.id)
          loadMeetingMessages(fallback.id)
        }
        return
      }

      const created = creation.value
      const fallback = deletedMeetingIdsRef.current.has(created.id) ? list[0] : created
      if (fallback && !activeMeetingIdRef.current) {
        setActiveMeetingId((current) => current || fallback.id)
        if (fallback.id !== created.id) loadMeetingMessages(fallback.id)
      }

      // 초기 목록 이후 사용한 회의는 다른 회의로 이동했더라도 정리하지 않는다.
      const stale = list.filter(
        (m) =>
          m.id !== created.id &&
          m.id !== linked &&
          m.id !== activeMeetingIdRef.current &&
          !openedMeetingIdsRef.current.has(m.id) &&
          isPrunable(m)
      )
      const staleIds = new Set(stale.map((m) => m.id))
      setMeetings((prev) => prev.filter((m) => !staleIds.has(m.id)))

      // 삭제는 화면을 그린 뒤 백그라운드로 — 실패해도 사용자 흐름을 막지 않는다
      for (const m of stale) {
        api.meetings.delete(m.id).catch(() => {})
      }
    }

    init()
  }, [user]) // eslint-disable-line react-hooks/exhaustive-deps

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
      openedMeetingIdsRef.current.add(meeting.id)
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
      openedMeetingIdsRef.current.add(id)
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

  const handleUpdateTitle = useCallback(async (id: string, title: string) => {
    openedMeetingIdsRef.current.add(id)
    if (!HAS_API) {
      setMeetings((prev) => prev.map((m) => (m.id === id ? { ...m, title } : m)))
      return
    }
    try {
      await api.meetings.updateTitle(id, title)
      setMeetings((prev) => prev.map((m) => (m.id === id ? { ...m, title } : m)))
    } catch {
      // Silent — user can retry
    }
  }, [])

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
      deletedMeetingIdsRef.current.add(id)
      setMeetings((prev) => prev.filter((m) => m.id !== id))
      if (activeMeetingIdRef.current === id) {
        const next = meetingsRef.current.find(
          (m) => m.id !== id && !deletedMeetingIdsRef.current.has(m.id)
        )
        activeMeetingIdRef.current = next?.id ?? ''
        setActiveMeetingId(next?.id ?? '')
        if (next) {
          if (HAS_API) loadMeetingMessages(next.id)
        }
      }
    },
    [loadMeetingMessages]
  )

  // ─── Task #5: WebSocket subtitle handler ───────────────────────────────────

  const handleWsMessage = useCallback(
    (msg: WsServerMessage) => {
      if (msg.type === 'stt_status') {
        setSttStatuses((previous) => ({ ...previous, [msg.provider]: msg }))
        return
      }
      if (msg.type === 'meeting_end') {
        // 서버는 그 미팅을 보고 있는 연결 전부에 보낸다 — 다른 미팅 이벤트도 상태엔 남겨 두고 표시만 거른다
        if (msg.meetingId)
          setMeetingEnd({
            meetingId: msg.meetingId,
            phase: msg.phase,
            status: msg.status,
            error: msg.error,
          })
        return
      }
      if (msg.type === 'audio_ready') {
        if (msg.url) {
          setAudioPrep(null)
          // Content-Disposition: attachment 라 페이지를 떠나지 않고 내려받는다
          window.location.href = msg.url
        } else {
          setAudioPrep({
            meetingId: msg.meetingId,
            pending: false,
            error: msg.error ?? '녹음 파일을 준비하지 못했습니다',
          })
        }
        return
      }
      if (msg.type === 'error') {
        // 이 분기가 없어서 백엔드 에러가 조용히 삼켜졌다 — STT 세션이 죽어도
        // 화면에는 아무 변화가 없어 마이크 문제로 오해하게 된다.
        console.error('[WS] server error:', msg.message)
        if (lastWsErrorRef.current !== msg.message) {
          lastWsErrorRef.current = msg.message
          alert(msg.message)
        }
        return
      }
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
        const MAX_SENTENCES = settings.maxSentencesPerBubble

        // 확정된 발화의 감지 언어를 헤더 배지에 반영한다. Whisper 모드는 partial 이
        // 없어서 이 경로가 유일한 갱신 지점이다. __pending__/__manual__/__quick__ 는
        // 우리가 보낸 방향을 되돌려주는 것이므로 근거가 아니다.
        if (msg.phase === 'done' && msg.detectedLanguage && !msg.messageId.startsWith('__')) {
          if (detectedLangRef.current !== msg.detectedLanguage) {
            detectedLangRef.current = msg.detectedLanguage
            setDetectedLang(msg.detectedLanguage)
          }
        }
        if (msg.phase === 'stt_partial') {
          // Word-by-word Transcribe partial — show in pending bubble, not committed messages
          const partialText = msg.originalText ?? ''
          const partialSpeaker = msg.speaker ?? 'speaker1'

          // AUTO 모드: 백엔드가 partial 에 실어 보낸 감지 언어로 방향을 잡는다.
          // 감지 언어가 바뀌면 이전 언어로 쌓인 partial 번역을 버려야 한다 —
          // 영어 번역 위에 한국어 번역이 이어붙으면 두 언어가 한 말풍선에 섞인다.
          const partialDetected = msg.detectedLanguage ?? detectedLangRef.current
          const langFlipped =
            settings.sourceLang === 'auto' &&
            partialDetected != null &&
            detectedLangRef.current != null &&
            partialDetected !== detectedLangRef.current
          if (partialDetected && partialDetected !== detectedLangRef.current) {
            detectedLangRef.current = partialDetected
            setDetectedLang(partialDetected)
          }
          if (langFlipped) {
            lastTranslatedPartialRef.current = ''
            pendingPartialRef.current = ''
            if (partialThrottleRef.current) {
              clearTimeout(partialThrottleRef.current)
              partialThrottleRef.current = null
            }
          }

          setPendingTranscript((prev) => ({
            messageId: msg.messageId,
            text: partialText,
            speaker: partialSpeaker,
            // 언어가 뒤집혔으면 이전 언어 번역은 버린다
            translation: langFlipped ? undefined : prev?.translation,
          }))

          const { source: srcLang, target: tgtLang } = resolveDirection(
            settings.sourceLang,
            settings.targetLang,
            partialDetected
          )

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
                      tgtLang,
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
                    tgtLang,
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
                  tgtLang,
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
              // 병합 조건 (같은 화자일 때만):
              // - 이전 말풍선이 미완성 문장(종결부호 없음) → 무조건 병합.
              //   잘린 문장을 이어붙이는 처리라 문장 수 상한/침묵 시간보다 우선한다.
              // - 이전 말풍선이 완성 문장 → 문장 수 상한 미만 + silenceTimeout 이내일 때만 병합
              const lastMsg = m.messages.at(-1)
              const isSentenceComplete = /[.?!。？！]\s*$/.test((lastMsg?.original ?? '').trimEnd())
              const timeDiff = lastMsg
                ? Date.now() - new Date(lastMsg.timestamp).getTime()
                : Infinity
              const canMerge =
                lastMsg &&
                lastMsg.speaker === streamSpeaker &&
                (!isSentenceComplete ||
                  (countSentences(lastMsg.original) < MAX_SENTENCES &&
                    timeDiff < SILENCE_TIMEOUT_MS))
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
          const isManual = msg.messageId.startsWith('__manual__')
          // 빠른 번역 팝업 스트리밍
          if (msg.messageId === '__quick__') {
            setQuickTranslateStream({ text: msg.partialTranslation ?? '', phase: 'translating' })
            return
          }
          // pending/append 자동 번역: translating 무시, done에서 한번에 업데이트
          if (msg.messageId === '__pending__' || msg.messageId === '__pending_append__') return
          // 자동 번역: translating phase 무시, done에서 한번에 업데이트
          if (!isManual) return
          const resolvedId = msg.messageId.slice('__manual__'.length)
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
          // 빠른 번역 팝업 완료
          if (msg.messageId === '__quick__') {
            setQuickTranslateStream({ text: msg.translatedText ?? '', phase: 'done' })
            return
          }
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
          setTimeout(() => {
            const updated = meetingsRef.current.find((mt) => mt.id === activeMeetingId)
            const meMsg = updated?.messages.find((mt) => mt.id === msg.messageId)
            if (meMsg?.translation) {
              const ctx = (updated?.messages ?? [])
                .filter((mt) => mt.original)
                .map((mt) => ({ speaker: mt.speaker, original: mt.original }))
              sendQaRequestRef.current(msg.messageId, meMsg.translation, ctx)
            }
          }, 0)
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
              m.id === activeMeetingId
                ? { ...m, summary: msg.summary ?? m.summary, title: msg.title ?? m.title }
                : m
            )
          )
          isSummarizingRef.current = false
          setIsSummarizing(false)
        } else if (msg.phase === 'error') {
          if (isSummarizingTimeoutRef.current) {
            clearTimeout(isSummarizingTimeoutRef.current)
            isSummarizingTimeoutRef.current = null
          }
          isSummarizingRef.current = false
          setIsSummarizing(false)
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? { ...m, summary: `요약 실패: ${msg.error ?? '알 수 없는 오류'}` }
                : m
            )
          )
        }
      } else if (msg.type === 'page_stream') {
        const targetId = msg.meetingId ?? pageGenMeetingIdRef.current
        if (!targetId) return
        if (msg.phase === 'unpublished') {
          unpublishingRef.current.delete(targetId)
          setUnpublishState((s) => (s?.meetingId === targetId ? null : s))
          setMeetings((prev) =>
            prev.map((m) => (m.id === targetId ? { ...m, page: undefined } : m))
          )
        } else if (msg.op === 'unpublish') {
          unpublishingRef.current.delete(targetId)
          setUnpublishState({
            meetingId: targetId,
            status: 'error',
            error: msg.error ?? '공유 해제에 실패했습니다',
          })
        } else if (msg.phase === 'status' || msg.phase === 'delta') {
          if (msg.started) {
            const alreadyTracking =
              pageGenByMeetingRef.current.get(targetId)?.status === 'generating'
            startPageGenFor(targetId)
            if (!alreadyTracking && targetId === activeMeetingIdRef.current) {
              setRightPanel('page')
              setSummaryOpen(true)
              setActiveTab('page')
              setHasNewPage(false)
            }
          }
          const line = (msg.text ?? '').trim()
          if (line) {
            // 이미 끝난(idle/error·타임아웃) 생성의 늦은 이벤트는 무시한다
            setPageGenFor(targetId, (g) =>
              g.status !== 'generating'
                ? g
                : {
                    ...g,
                    status: 'generating',
                    log: msg.phase === 'status' ? [...g.log, line] : g.log,
                  }
            )
          }
        } else if (msg.phase === 'done') {
          setPageGenFor(targetId, PAGE_GEN_IDLE)
          if (msg.page) {
            const isNewPage =
              meetingsRef.current.find((m) => m.id === targetId)?.page?.pageId !== msg.page.pageId
            setMeetings((prev) =>
              prev.map((m) => (m.id === targetId ? { ...m, page: msg.page } : m))
            )
            if (isNewPage && activeTab !== 'page') setHasNewPage(true)
          }
        } else if (msg.phase === 'error') {
          setPageGenFor(targetId, (g) => ({
            ...g,
            status: 'error',
            error: msg.error ?? '알 수 없는 오류',
          }))
        }
      } else if (msg.type === 'qa_stream') {
        const qaMsgId = msg.messageId
        if (msg.phase === 'delta') {
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === qaMsgId
                        ? {
                            ...existing,
                            qaResponse: (existing.qaResponse ?? '') + (msg.text ?? ''),
                            qaStreamPhase: 'streaming' as const,
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
                      existing.id === qaMsgId
                        ? {
                            ...existing,
                            qaResponse: msg.answer ?? existing.qaResponse,
                            qaStreamPhase: 'done' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
        } else if (msg.phase === 'error') {
          setMeetings((prev) =>
            prev.map((m) =>
              m.id === activeMeetingId
                ? {
                    ...m,
                    messages: m.messages.map((existing) =>
                      existing.id === qaMsgId
                        ? {
                            ...existing,
                            qaResponse: msg.error ?? 'Q&A 실패',
                            qaStreamPhase: 'error' as const,
                          }
                        : existing
                    ),
                  }
                : m
            )
          )
        }
      }
    },
    [
      activeMeetingId,
      activeTab,
      settings.silenceTimeout,
      settings.maxSentencesPerBubble,
      settings.ttsAutoPlay,
      settings.partialTranslationMode,
      settings.partialThrottleMs,
      settings.sourceLang,
      settings.targetLang,
      settings.translationModel,
      setPageGenFor,
      startPageGenFor,
    ]
  )

  const {
    status: wsStatus,
    connect,
    disconnect,
    sendAudio,
    startRecording,
    stopRecording,
    prepareStt,
    requestSttStatus,
    sendSummarize,
    sendTranslate,
    sendTtsRequest,
    sendQaRequest,
    sendGeneratePage,
    sendPageStatus,
    sendUnpublishPage,
    sendEndMeeting,
    sendPrepareAudio,
  } = useWebSocket({
    meetingId: activeMeetingId,
    onMessage: handleWsMessage,
  })

  // sendTranslateRef 업데이트 (handleWsMessage 선언 이후에 useWebSocket이 오므로 ref로 전달)
  useEffect(() => {
    sendTranslateRef.current = sendTranslate
  }, [sendTranslate])

  useEffect(() => {
    sendQaRequestRef.current = sendQaRequest
  }, [sendQaRequest])

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
    flush: flushAudio,
  } = useAudioCapture({
    onChunk: handleChunk,
    chunkDurationMs: sttChunkDuration(settings.sttProvider),
    audioSource: settings.audioSource,
  })

  const recordingAction = sttRecordingAction(settings.sttProvider, selectedSttStatus, isRecording)
  const prepareSelectedStt = useCallback(
    (provider: SttProvider = settings.sttProvider) => {
      const sent = prepareStt(provider)
      setSttStatuses((previous) => ({
        ...previous,
        [provider]: {
          provider,
          status: sent ? 'starting' : 'error',
          message: sent
            ? '음성 인식 엔진을 준비하고 있습니다. 몇 분 걸릴 수 있습니다.'
            : '서버에 연결한 뒤 다시 준비해 주세요.',
        },
      }))
    },
    [prepareStt, settings.sttProvider]
  )

  useEffect(() => {
    if (wsStatus !== 'connected' || !isManagedStt(settings.sttProvider)) return
    requestSttStatus(settings.sttProvider)
    const timer = setInterval(() => requestSttStatus(settings.sttProvider), 5000)
    return () => clearInterval(timer)
  }, [wsStatus, settings.sttProvider, requestSttStatus])

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

  // 언어 또는 엔진을 변경하면 기존 발화를 마무리하고 새 설정으로 인식한다.
  const prevSourceLangRef = useRef(settings.sourceLang)
  const prevSttProviderRef = useRef(settings.sttProvider)
  useEffect(() => {
    const prev = prevSourceLangRef.current
    const prevProvider = prevSttProviderRef.current
    prevSourceLangRef.current = settings.sourceLang
    prevSttProviderRef.current = settings.sttProvider
    if (prev === settings.sourceLang && prevProvider === settings.sttProvider) return
    if (!isRecording || wsStatus !== 'connected') return

    console.warn(
      '[WS] speech settings changed during recording (%s/%s → %s/%s)',
      prevProvider,
      prev,
      settings.sttProvider,
      settings.sourceLang
    )
    // 이전 언어로 쌓인 partial 은 버린다 — 새 언어 결과와 섞이면 안 된다
    setPendingTranscript(null)
    lastTranslatedPartialRef.current = ''
    pendingPartialRef.current = ''
    // 감지 배지도 리셋 — 이전 모드의 감지값이 남으면 AUTO 진입 직후 오해를 준다
    detectedLangRef.current = null
    setDetectedLang(null)
    if (partialThrottleRef.current) {
      clearTimeout(partialThrottleRef.current)
      partialThrottleRef.current = null
    }
    // WebSocket preserves send order: buffered audio belongs to the old session.
    flushAudio()
    startRecording({
      sttProvider: settings.sttProvider,
      sourceLang: settings.sourceLang,
      targetLang: settings.targetLang,
      modelId: settings.translationModel,
      speaker: 'speaker1',
      translationTiming: settings.translationTiming,
    })
  }, [
    settings.sourceLang,
    isRecording,
    wsStatus,
    startRecording,
    flushAudio,
    settings.sttProvider,
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

  useEffect(() => {
    // 미팅을 바꾸면 그 미팅의 생성 상태를 복원한다 (생성 중이던 미팅으로 돌아오면 "생성 중")
    setPageGen(pageGenByMeetingRef.current.get(activeMeetingId) ?? PAGE_GEN_IDLE)
  }, [activeMeetingId])

  // 마이크를 끄고 화면의 진행 중 자막 상태를 정리한다 (서버에 보내는 정지 신호는 호출부가 고른다)
  const resetLocalRecording = useCallback(() => {
    stopAudio()
    setPendingTranscript(null)
    if (partialThrottleRef.current) {
      clearTimeout(partialThrottleRef.current)
      partialThrottleRef.current = null
    }
    pendingPartialRef.current = ''
    translationTimeoutsRef.current.forEach((t) => clearTimeout(t))
    translationTimeoutsRef.current.clear()
  }, [stopAudio])

  useEffect(() => {
    if (isRecording && shouldStopSttRecording(settings.sttProvider, selectedSttStatus)) {
      resetLocalRecording()
      stopRecording()
    }
  }, [isRecording, settings.sttProvider, selectedSttStatus, resetLocalRecording, stopRecording])

  const handleSttSelect = useCallback(
    (provider: SttProvider) => {
      if (isRecording && provider === settings.sttProvider) return
      if (isRecording) {
        resetLocalRecording()
        stopRecording()
      }
      updateSettings({ sttProvider: provider })
      if (isManagedStt(provider)) prepareSelectedStt(provider)
    },
    [
      isRecording,
      settings.sttProvider,
      resetLocalRecording,
      stopRecording,
      updateSettings,
      prepareSelectedStt,
    ]
  )

  const handleToggleRecording = useCallback(async () => {
    if (HAS_API && !activeMeetingId && !isRecording) return
    if (recordingAction === 'prepare') {
      prepareSelectedStt()
      return
    }
    if (recordingAction === 'wait') return
    if (isRecording) {
      resetLocalRecording()
      stopRecording()
      // 녹음 종료 시 자동 요약 (autoSummarizeMessageCount > 0 일 때)
      if (settings.autoSummarizeMessageCount > 0) {
        handleSummarizeRef.current()
      }
    } else {
      if (!(await startAudio())) return
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
    activeMeetingId,
    isRecording,
    startAudio,
    resetLocalRecording,
    startRecording,
    stopRecording,
    settings.sttProvider,
    settings.sourceLang,
    settings.targetLang,
    settings.translationModel,
    settings.translationTiming,
    settings.autoSummarizeMessageCount,
    recordingAction,
    prepareSelectedStt,
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

      const currentMessages =
        meetingsRef.current.find((m) => m.id === activeMeetingId)?.messages ?? []
      const msgCount = currentMessages.length

      if (msgCount === 0) return

      // Navigate when the user asks for a summary, not when an asynchronous
      // result arrives: background updates must not interrupt writing notes.
      if (force) {
        setRightPanel('summary')
        setSummaryOpen(true)
      }

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
        isSummarizingRef.current = false
        setIsSummarizing(false)
        return
      }

      // WS 경로: connected이고 실제로 send 성공한 경우에만
      if (wsStatus === 'connected') {
        const wsMessages = currentMessages
          .filter((m) => m.original)
          .map((m) => ({ speaker: m.speaker, original: m.original, timestamp: m.timestamp }))
        const sent = sendSummarize(activeMeetingId, wsMessages)
        if (sent) {
          setMeetings((prev) =>
            prev.map((m) => (m.id === activeMeetingId ? { ...m, summary: '' } : m))
          )
          lastSummarizedCountRef.current[activeMeetingId] = msgCount
          // Safety: 5분 후 강제 리셋 (WS done/error 미수신 대비). 상세 요약은 긴 미팅에서 1~3분 걸린다.
          if (isSummarizingTimeoutRef.current) clearTimeout(isSummarizingTimeoutRef.current)
          isSummarizingTimeoutRef.current = setTimeout(() => {
            isSummarizingRef.current = false
            setIsSummarizing(false)
          }, 300_000)
          return
        }
        // WS status는 connected이지만 실제 소켓 not OPEN → REST fallback
      }

      // REST API fallback
      try {
        const { summary, title } = await api.meetings.summarize(activeMeetingId)
        lastSummarizedCountRef.current[activeMeetingId] = msgCount
        setMeetings((prev) =>
          prev.map((m) =>
            m.id === activeMeetingId
              ? { ...m, summary: parseSummary(summary), title: title ?? m.title }
              : m
          )
        )
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

  // ─── 미팅 종료 · 녹음 파일 ─────────────────────────────────────────────────

  // 서버가 녹음 정지 → 오디오 합치기 → 요약 → 페이지 생성·메일을 끝까지 처리한다.
  const handleEndMeeting = useCallback(() => {
    if (!activeMeetingId) return
    if (isRecording && !window.confirm('녹음을 멈추고 회의를 종료할까요?')) return
    const currentMessages =
      meetingsRef.current.find((m) => m.id === activeMeetingId)?.messages ?? []
    const wsMessages = currentMessages
      .filter((m) => m.original)
      .map((m) => ({
        speaker: m.speaker,
        original: m.original,
        translation: m.translation,
        timestamp: m.timestamp,
        detectedLanguage: m.detectedLanguage,
      }))
    // endMeeting closes server-side audio intake, so enqueue the buffered tail first.
    if (isRecording) flushAudio()
    if (!sendEndMeeting(activeMeetingId, settings.emailOnEnd, wsMessages)) {
      setMeetingEnd({
        meetingId: activeMeetingId,
        phase: 'error',
        error: '서버와 연결되어 있지 않습니다.',
      })
      return
    }
    if (isRecording) resetLocalRecording()
    const meetingId = activeMeetingId
    setMeetingEnd({ meetingId, phase: 'audio' })
    // 진행 중에 다른 미팅으로 옮기거나 소켓이 끊기면 결과 이벤트를 못 받는다 — 버튼이 "종료 중"에 갇히지 않게.
    // 서버 쪽 처리(요약 최대 수 분 + 메일)는 그와 무관하게 끝까지 간다.
    setTimeout(() => {
      setMeetingEnd((s) =>
        s?.meetingId === meetingId && (s.phase === 'audio' || s.phase === 'summary')
          ? {
              meetingId,
              phase: 'error',
              error: '종료 결과를 받지 못했습니다. 메일함이나 요약 패널을 확인해 주세요.',
            }
          : s
      )
    }, 360_000)
    // 서버 요약이 summary_stream 으로 흘러 들어온다 — 요약 패널을 열어 둔다
    if (currentMessages.length > 0) {
      lastSummarizedCountRef.current[activeMeetingId] = currentMessages.length
      setMeetings((prev) => prev.map((m) => (m.id === activeMeetingId ? { ...m, summary: '' } : m)))
      setRightPanel('summary')
      setSummaryOpen(true)
    }
  }, [
    activeMeetingId,
    isRecording,
    flushAudio,
    sendEndMeeting,
    settings.emailOnEnd,
    resetLocalRecording,
  ])

  const handleDownloadAudio = useCallback(() => {
    if (!activeMeetingId) return
    if (!sendPrepareAudio(activeMeetingId)) {
      setAudioPrep({
        meetingId: activeMeetingId,
        pending: false,
        error: '서버와 연결되어 있지 않습니다.',
      })
      return
    }
    const meetingId = activeMeetingId
    setAudioPrep({ meetingId, pending: true })
    // 응답을 놓치면(재접속 등) 버튼이 "준비 중"에 갇히지 않게 — 1시간 녹음 합치기도 수십 초면 끝난다
    setTimeout(() => {
      setAudioPrep((p) =>
        p?.meetingId === meetingId && p.pending
          ? { meetingId, pending: false, error: '응답이 없습니다. 다시 시도해 주세요.' }
          : p
      )
    }, 120_000)
  }, [activeMeetingId, sendPrepareAudio])

  // ─── Meeting page (AgentCore Harness) ───────────────────────────────────────

  const handleGeneratePage = useCallback(() => {
    if (!activeMeetingId || !HAS_API) return
    const meetingId = activeMeetingId
    if (pageGenByMeetingRef.current.get(meetingId)?.status === 'generating') return
    const meeting = meetingsRef.current.find((m) => m.id === meetingId)
    const currentMessages = meeting?.messages ?? []
    if (currentMessages.length === 0) return
    // 녹음 중에는 DynamoDB 에 아직 없는 메시지가 있을 수 있어 인라인으로 함께 보낸다
    const inline = isRecording
      ? currentMessages
          .filter((m) => m.original)
          .map((m) => ({
            speaker: m.speaker,
            original: m.original,
            translation: m.translation,
            timestamp: m.timestamp,
            detectedLanguage: m.detectedLanguage,
          }))
      : undefined
    const sent = sendGeneratePage(meetingId, inline)
    if (!sent) {
      setPageGenFor(meetingId, {
        status: 'error',
        log: [],
        error: '서버와 연결되어 있지 않습니다.',
      })
      return
    }
    startPageGenFor(meetingId)
    setRightPanel('page')
    setSummaryOpen(true)
    setActiveTab('page')
  }, [activeMeetingId, isRecording, sendGeneratePage, setPageGenFor, startPageGenFor])

  const handleUnpublishPage = useCallback(() => {
    const meetingId = activeMeetingId
    // 중복 클릭 방지: 응답이 올 때까지 같은 미팅의 해제 요청은 한 번만 보낸다
    if (!meetingId || unpublishingRef.current.has(meetingId)) return
    if (!sendUnpublishPage(meetingId)) {
      setUnpublishState({ meetingId, status: 'error', error: '서버와 연결되어 있지 않습니다.' })
      return
    }
    unpublishingRef.current.add(meetingId)
    pageGenMeetingIdRef.current = meetingId
    setUnpublishState({ meetingId, status: 'pending' })
  }, [activeMeetingId, sendUnpublishPage])

  // 생성 중인 미팅의 실제 결과를 API 로 확인한다 — 소켓이 끊기면 done 이벤트를 못 받을 수 있다
  const reconcilePageGen = useCallback(async () => {
    const currentId = activeMeetingIdRef.current
    // 종료 후 요약을 기다리다 연결이 끊기면 시작 이벤트 자체를 놓칠 수 있다.
    if (currentId && pageGenByMeetingRef.current.get(currentId)?.status !== 'generating') {
      sendPageStatus(currentId, true)
    }
    const pending = Array.from(pageGenByMeetingRef.current.entries()).filter(
      ([, g]) => g.status === 'generating'
    )
    for (const [meetingId, g] of pending) {
      try {
        const page = (await api.meetings.get(meetingId)).page
        const isNew =
          !!page &&
          page.pageId !== g.prevPageId &&
          Date.parse(page.generatedAt) >= (g.startedAt ?? 0) - PAGE_GEN_CLOCK_SKEW_MS
        if (isNew) {
          setMeetings((prev) => prev.map((m) => (m.id === meetingId ? { ...m, page } : m)))
          setPageGenFor(meetingId, PAGE_GEN_IDLE)
          continue
        }
      } catch {
        // 다음 주기에 다시 확인한다
      }
      // 새 페이지가 없으면 서버에 묻는다 — 실패(error)는 DynamoDB 에 남지 않아 API 로는 알 수 없다
      sendPageStatus(meetingId)
    }
  }, [setPageGenFor, sendPageStatus])

  useEffect(() => {
    if (!HAS_API) return
    const id = setInterval(() => void reconcilePageGen(), PAGE_GEN_POLL_MS)
    return () => clearInterval(id)
  }, [reconcilePageGen])

  const prevPageWsStatusRef = useRef<typeof wsStatus>(wsStatus)
  useEffect(() => {
    const prev = prevPageWsStatusRef.current
    prevPageWsStatusRef.current = wsStatus
    if (wsStatus === 'connected' && prev !== 'connected') {
      void reconcilePageGen()
    } else if (wsStatus !== 'connected' && unpublishingRef.current.size > 0) {
      // 연결이 끊기면 해제 응답을 받을 수 없다 — 다시 시도할 수 있게 풀어 준다
      unpublishingRef.current.clear()
      setUnpublishState((s) =>
        s?.status === 'pending'
          ? { ...s, status: 'error', error: '연결이 끊겼습니다. 다시 시도하세요.' }
          : s
      )
    }
  }, [wsStatus, reconcilePageGen])

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
      // 'me' speaker: 항상 KO → EN (내가 입력한 한국어를 영어 음성으로 내보내는 경로)
      // 그 외: AUTO 면 저장된 감지 언어의 반대편으로, 수동이면 설정된 방향으로.
      const isMe = speaker === 'me'
      const dir = resolveDirection(
        settings.sourceLang,
        settings.targetLang,
        detectedLanguage ?? detectedLangRef.current
      )
      const sourceLang = isMe ? 'ko' : (detectedLanguage ?? dir.source)
      const targetLang = isMe ? 'en' : dir.target
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
    if (!ttsInput.trim() || isTtsPending || (HAS_API && !activeMeetingId)) return
    const text = ttsInput.trim()
    setTtsInput('')
    setRightPanel('notes')
    setSummaryOpen(true)
    setActiveTab('notes')
    setHasNewNotes(false)

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

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1024px)')
    const closeDrawer = () => {
      setDesktopLayout(desktop.matches)
      if (desktop.matches) setSidebarOpen(false)
    }
    closeDrawer()
    desktop.addEventListener('change', closeDrawer)
    return () => desktop.removeEventListener('change', closeDrawer)
  }, [])

  // ─── Render ──────────────────────────────────────────────────────────────────

  // Auth gate — show loading spinner while session is being restored
  if (HAS_COGNITO && authLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-white dark:bg-slate-950">
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

  const changeDirection = (source: 'en' | 'ko' | 'auto') => {
    updateSettings(
      source === 'auto'
        ? { sourceLang: 'auto' }
        : { sourceLang: source, targetLang: source === 'en' ? 'ko' : 'en' }
    )
  }

  const changeTab = (tab: 'voice' | 'notes' | 'summary' | 'page') => {
    setActiveTab(tab)
    if (tab === 'voice') setHasNewVoice(false)
    else {
      setRightPanel(tab)
      setSummaryOpen(true)
      if (tab === 'notes') setHasNewNotes(false)
      if (tab === 'page') setHasNewPage(false)
    }
  }

  const closeUtility = () => {
    setSummaryOpen(false)
    setActiveTab('voice')
  }

  const sidebarProps = {
    meetings,
    activeMeetingId,
    onSelect: handleSelectMeeting,
    onClose: () => setSidebarOpen(false),
    onNewMeeting: handleNewMeeting,
    onDelete: handleDeleteMeeting,
    onUpdateTitle: handleUpdateTitle,
    onGenerateTitle: handleGenerateTitle,
    generatingTitleId,
    isCreating: isCreatingMeeting,
    isLoading: isLoadingMeetings,
    loadError: meetingListError,
  }

  const meetingDate =
    activeMeeting?.startedAt && !Number.isNaN(Date.parse(activeMeeting.startedAt))
      ? new Date(activeMeeting.startedAt).toLocaleDateString('ko-KR', {
          month: 'long',
          day: 'numeric',
          weekday: 'short',
        })
      : null
  const voiceCount =
    activeMeeting?.messages.filter((message) => message.speaker !== 'me').length ?? 0
  const panelLabels = { notes: '내 메모', summary: '요약', page: '회의 페이지' }
  const visibleUtilityTab = getVisibleUtilityTab(desktopLayout, activeTab, rightPanel)
  const activeMeetingEnd = meetingEnd?.meetingId === activeMeetingId ? meetingEnd : null
  const activeAudioPrep = audioPrep?.meetingId === activeMeetingId ? audioPrep : null
  const floatingAvoidRef = desktopLayout
    ? undefined
    : activeTab === 'notes'
      ? notesComposerRef
      : activeTab === 'page' && activeMeeting?.page && pageGen.status === 'idle'
        ? pageFooterRef
        : undefined

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-canvas font-sans">
      <Header
        isRecording={isRecording}
        wsStatus={HAS_API ? wsStatus : undefined}
        onToggleSidebar={() => setSidebarOpen((value) => !value)}
        sidebarOpen={sidebarOpen}
        onToggleQuickTranslate={() => setQuickTranslateOpen((value) => !value)}
        quickTranslateOpen={quickTranslateOpen}
        onToggleSettings={() => setSettingsOpen((value) => !value)}
        settingsOpen={settingsOpen}
        onLogout={HAS_COGNITO ? logout : undefined}
        sourceLang={settings.sourceLang}
        detectedLang={detectedLang}
        onChangeLangDirection={changeDirection}
      />

      <div className="flex min-h-0 flex-1">
        <aside className="hidden shrink-0 lg:block" aria-label="회의 목록">
          <MeetingSidebar {...sidebarProps} headingId="desktop-meeting-list-title" />
        </aside>

        <main className="relative flex min-w-0 flex-1 flex-col">
          <div className="meeting-heading shrink-0">
            <div className="flex h-12 items-center gap-2 px-4 sm:px-5">
              <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <div className="min-w-0 flex-1">
                  {activeMeeting ? (
                    <EditableTitle
                      key={activeMeeting.id}
                      title={activeMeeting.title}
                      onSave={(newTitle) => handleUpdateTitle(activeMeeting.id, newTitle)}
                      className="min-w-0 text-sm font-semibold tracking-tight text-ink"
                    />
                  ) : (
                    <h1 className="truncate text-sm font-semibold text-ink">새 회의</h1>
                  )}
                </div>
                <div className="meeting-metadata flex shrink-0 items-center gap-2 whitespace-nowrap text-[0.6875rem] text-muted">
                  {activeMeeting ? (
                    <>
                      {meetingDate && (
                        <time dateTime={activeMeeting.startedAt} title={meetingDate}>
                          <span className="sm:hidden">
                            {new Date(activeMeeting.startedAt)
                              .toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' })
                              .replace(/\s/g, '')
                              .replace(/\.$/, '')}
                          </span>
                          <span className="hidden sm:inline">{meetingDate}</span>
                        </time>
                      )}
                      <span
                        className="flex items-center gap-1"
                        aria-label={`대화 ${activeMeeting.messages.length}개`}
                        title={`대화 ${activeMeeting.messages.length}개`}
                      >
                        <Icon name="message" className="h-3 w-3" />
                        {activeMeeting.messages.length}
                      </span>
                    </>
                  ) : (
                    <span role="status">
                      {isLoadingMeetings || isCreatingMeeting
                        ? '회의 준비 중…'
                        : '새 회의를 만들어 시작하세요'}
                    </span>
                  )}
                </div>
              </div>
              <div className="meeting-language w-[6rem] shrink-0 sm:hidden">
                <LanguageDirection
                  compact
                  sourceLang={settings.sourceLang}
                  onChange={changeDirection}
                />
              </div>
              <div className="hidden items-center gap-2 lg:flex">
                {!summaryOpen && (
                  <button
                    type="button"
                    className="ui-button ui-button-secondary"
                    onClick={() => setSummaryOpen(true)}
                    aria-label="회의 도구 펼치기"
                  >
                    <Icon name="panelRight" />
                    메모 · 요약
                  </button>
                )}
              </div>
            </div>
            {(activeMeeting?.brief || isBriefRefreshing) && (
              <div
                role="status"
                aria-label="회의 한 줄 요약"
                aria-busy={isBriefRefreshing}
                className="flex items-start gap-2 px-4 pb-3 text-xs leading-5 text-muted sm:px-5"
              >
                <Icon
                  name="sparkles"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-600 dark:text-accent-400"
                />
                <p className="min-w-0 break-words">
                  {activeMeeting?.brief ?? '회의 내용을 한 줄로 정리하고 있어요…'}
                </p>
              </div>
            )}
          </div>

          <MobileTabBar
            activeTab={activeTab}
            onTabChange={changeTab}
            hasNewVoice={hasNewVoice}
            hasNewNotes={hasNewNotes}
            hasNewPage={hasNewPage}
          />

          <div className="flex min-h-0 flex-1 gap-3 px-3 pb-3 sm:px-5 sm:pb-5">
            <section
              id="meeting-voice-panel"
              role="tabpanel"
              aria-label="대화"
              aria-busy={activeMeetingLoadState === 'loading'}
              className={`ui-surface min-w-0 flex-1 flex-col overflow-hidden ${activeTab === 'voice' ? 'flex' : 'hidden lg:flex'}`}
            >
              <div className="ui-panel-header">
                <div className="flex items-center gap-2">
                  <Icon name="waveform" className="h-4 w-4 text-accent-600 dark:text-accent-400" />
                  <h2 className="text-xs font-semibold text-ink">대화</h2>
                  {voiceCount > 0 && (
                    <span className="text-[0.6875rem] tabular-nums text-muted">{voiceCount}</span>
                  )}
                </div>
                <VoiceFontSizeControl
                  fontSize={settings.voiceFontSize}
                  onChange={(voiceFontSize) => updateSettings({ voiceFontSize })}
                />
              </div>
              {activeMeetingLoadState ? (
                <MeetingContentStatus
                  status={activeMeetingLoadState}
                  onRetry={() => loadMeetingMessages(activeMeetingId)}
                />
              ) : (
                <VoiceArea
                  key={activeMeetingId}
                  isVisible={desktopLayout || activeTab === 'voice'}
                  messages={activeMeeting?.messages ?? []}
                  isRecording={isRecording}
                  isProcessing={isTtsPending}
                  playingMessageId={playingMessageId}
                  isMessageLoading={isMessageLoading}
                  onPlayMessage={handlePlayMessage}
                  onStopMessage={handleStopAllAudio}
                  onTranslateMessage={handleTranslateMessage}
                  pendingTranscript={pendingTranscript}
                  fontSize={settings.voiceFontSize}
                />
              )}
            </section>

            {summaryOpen && (
              <aside
                aria-label="회의 도구"
                className={`relative min-w-0 w-full shrink-0 flex-col lg:w-[var(--utility-width)] lg:max-w-[70%] ${activeTab === 'voice' ? 'hidden lg:flex' : 'flex'}`}
                data-summary-width={summaryWidth}
                style={{ '--utility-width': `${summaryWidth}px` } as CSSProperties}
              >
                <div
                  role="separator"
                  aria-label="회의 도구 너비 조절"
                  aria-orientation="vertical"
                  aria-valuemin={summaryMinWidth}
                  aria-valuemax={summaryMaxWidth}
                  aria-valuenow={summaryWidth}
                  tabIndex={0}
                  onPointerDown={handleSummaryResize}
                  onKeyDown={handleResizeKeyDown}
                  className="absolute -left-2 top-3 bottom-3 z-10 hidden w-1.5 cursor-col-resize touch-none rounded-full transition-colors hover:bg-accent-300 focus-visible:bg-accent-300 lg:block"
                />
                <div className="ui-surface flex min-h-0 flex-1 flex-col overflow-hidden">
                  <div className="hidden min-h-12 shrink-0 items-center gap-1 border-b border-subtle px-2 py-1.5 lg:flex">
                    <Tabs
                      label="회의 도구"
                      panelId="meeting-utility-panel"
                      value={rightPanel}
                      onChange={changeTab}
                      className="flex-1 bg-transparent [&_.ui-tab]:flex-auto [&_.ui-tab]:px-1.5"
                      items={[
                        { value: 'notes', label: '내 메모', badge: hasNewNotes },
                        { value: 'summary', label: '요약' },
                        {
                          value: 'page',
                          label: '회의 페이지',
                          badge: hasNewPage || pageGen.status === 'generating',
                        },
                      ]}
                    />
                    <button
                      type="button"
                      onClick={closeUtility}
                      className="ui-icon-button h-8 w-8 min-h-8"
                      aria-label="회의 도구 접기"
                      title="회의 도구 접기"
                    >
                      <Icon name="panelRight" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div
                    id="meeting-utility-panel"
                    role="tabpanel"
                    aria-label={panelLabels[visibleUtilityTab]}
                    aria-busy={activeMeetingLoadState === 'loading'}
                    className="flex min-h-0 flex-1 flex-col"
                  >
                    {activeMeetingLoadState ? (
                      <MeetingContentStatus
                        status={activeMeetingLoadState}
                        loadingMessage={`${panelLabels[visibleUtilityTab]} 불러오는 중…`}
                        onRetry={() => loadMeetingMessages(activeMeetingId)}
                      />
                    ) : visibleUtilityTab === 'notes' ? (
                      <>
                        <NotesArea
                          key={activeMeetingId}
                          isVisible={desktopLayout || activeTab === 'notes'}
                          messages={activeMeeting?.messages ?? []}
                          playingMessageId={playingMessageId}
                          isMessageLoading={isMessageLoading}
                          onPlayMessage={handlePlayMessage}
                          onStopMessage={handleStopAllAudio}
                          onTranslateMessage={handleTranslateMessage}
                        />
                        <NotesComposer
                          formRef={notesComposerRef}
                          value={ttsInput}
                          onChange={setTtsInput}
                          onSend={handleSend}
                          onStop={handleStopAllAudio}
                          isPending={isTtsPending}
                          disabled={HAS_API && !activeMeetingId}
                        />
                      </>
                    ) : visibleUtilityTab === 'summary' ? (
                      <SummaryPanel
                        key={activeMeetingId}
                        summary={activeMeeting?.summary}
                        messages={activeMeeting?.messages}
                        meetingTitle={activeMeeting?.title}
                        onSummarize={() => handleSummarize(true)}
                        isSummarizing={isSummarizing}
                      />
                    ) : (
                      <MeetingPagePanel
                        key={activeMeetingId}
                        footerRef={pageFooterRef}
                        page={activeMeeting?.page}
                        gen={pageGen}
                        hasMessages={(activeMeeting?.messages?.length ?? 0) > 0}
                        wsConnected={wsStatus === 'connected'}
                        unpublishing={
                          unpublishState?.meetingId === activeMeetingId &&
                          unpublishState.status === 'pending'
                        }
                        unpublishError={
                          unpublishState?.meetingId === activeMeetingId &&
                          unpublishState.status === 'error'
                            ? unpublishState.error
                            : undefined
                        }
                        onGenerate={handleGeneratePage}
                        onUnpublish={handleUnpublishPage}
                      />
                    )}
                  </div>
                </div>
              </aside>
            )}
          </div>

          <ControlPanel
            key={activeMeetingId}
            avoidElementRef={floatingAvoidRef}
            isRecording={isRecording}
            onToggleRecording={handleToggleRecording}
            recordingDisabled={(HAS_API && !activeMeetingId) || recordingAction === 'wait'}
            recordingLabel={
              recordingAction === 'prepare'
                ? '엔진 준비'
                : recordingAction === 'wait'
                  ? '엔진 준비 중'
                  : undefined
            }
            audioLevel={audioLevel}
            audioSource={settings.audioSource}
            onEndMeeting={HAS_API && activeMeetingId ? handleEndMeeting : undefined}
            isEndingMeeting={isEnding(activeMeetingEnd)}
            endMeetingDisabled={wsStatus !== 'connected'}
            meetingActions={
              HAS_API && activeMeetingId ? (
                <MeetingActions
                  wsConnected={wsStatus === 'connected'}
                  audioPrep={activeAudioPrep}
                  onDownloadAudio={handleDownloadAudio}
                />
              ) : undefined
            }
            status={
              <>
                {isManagedStt(settings.sttProvider) && (
                  <div className="ui-surface pointer-events-auto max-w-[min(24rem,calc(100vw-4rem))] px-3 pb-3 pt-1 shadow-lg">
                    <SttStatusPanel
                      state={selectedSttStatus}
                      connected={wsStatus === 'connected'}
                      compact
                    />
                  </div>
                )}
                {HAS_API && activeMeetingId && (
                  <MeetingStatus endState={activeMeetingEnd} audioPrep={activeAudioPrep} />
                )}
              </>
            }
          />
        </main>
      </div>

      {sidebarOpen && (
        <Dialog
          labelledBy="meeting-list-title"
          onClose={() => setSidebarOpen(false)}
          className="ui-drawer"
        >
          <MeetingSidebar {...sidebarProps} />
        </Dialog>
      )}
      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          onUpdate={updateSettings}
          sttStatus={selectedSttStatus}
          sttConnected={wsStatus === 'connected'}
          onSttSelect={handleSttSelect}
          onPrepareStt={() => prepareSelectedStt()}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {quickTranslateOpen && (
        <QuickTranslatePopup
          settings={settings}
          onClose={() => setQuickTranslateOpen(false)}
          sendTranslate={sendTranslate}
          wsConnected={wsStatus === 'connected'}
          stream={quickTranslateStream}
          onResetStream={() => setQuickTranslateStream(null)}
        />
      )}
    </div>
  )
}
