'use client'

import { useState } from 'react'
import Header from '@/components/Header'
import MeetingSidebar from '@/components/MeetingSidebar'
import ChatArea from '@/components/ChatArea'
import SummaryPanel from '@/components/SummaryPanel'
import ControlPanel from '@/components/ControlPanel'
import { Meeting } from '@/types/meeting'

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
      {
        id: '4',
        speaker: 'speaker1',
        original: 'Perfect. We should focus on the conversion rate improvements this quarter.',
        translation: '좋아요. 이번 분기 전환율 개선에 집중해야 합니다.',
        timestamp: '2026-03-17T09:03:10',
      },
      {
        id: '5',
        speaker: 'me',
        original: 'We achieved a 12% increase in conversions. Mostly from the onboarding redesign.',
        translation: '전환율이 12% 증가했습니다. 주로 온보딩 재설계 덕분입니다.',
        timestamp: '2026-03-17T09:03:45',
      },
    ],
    summary: [
      'Q1 전환율 12% 증가 달성',
      '온보딩 재설계가 주요 성과 요인',
      '제품 리뷰 대시보드 공유 완료',
      'Q2 목표: 지속적인 전환율 최적화 및 리텐션 개선',
    ],
  },
  {
    id: 'm2',
    title: 'Design Sync',
    startedAt: '2026-03-16T14:00:00',
    messages: [
      {
        id: '6',
        speaker: 'speaker1',
        original: 'The new design system looks fantastic! Great work on the components.',
        translation: '새 디자인 시스템이 정말 훌륭합니다! 컴포넌트 작업 정말 잘했네요.',
        timestamp: '2026-03-16T14:01:00',
      },
      {
        id: '7',
        speaker: 'me',
        original: 'Thanks! We spent two weeks on the component library. It should speed things up.',
        translation: '감사합니다! 컴포넌트 라이브러리에 2주를 투자했어요. 작업이 빨라질 거예요.',
        timestamp: '2026-03-16T14:01:30',
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

export default function Home() {
  const [meetings] = useState<Meeting[]>(MOCK_MEETINGS)
  const [activeMeetingId, setActiveMeetingId] = useState('m1')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [ttsInput, setTtsInput] = useState('')

  const activeMeeting = meetings.find((m) => m.id === activeMeetingId) ?? meetings[0]

  const handleSelectMeeting = (id: string) => {
    setActiveMeetingId(id)
    setSidebarOpen(false)
  }

  const handleSend = () => {
    if (!ttsInput.trim()) return
    // TODO: KO -> EN translation + TTS via Amazon Polly
    setTtsInput('')
  }

  return (
    <main className="relative flex flex-col h-screen overflow-hidden font-sans">
      {/* Background */}
      <div className="absolute inset-0 -z-10 bg-gradient-to-br from-slate-50 via-indigo-50/60 to-violet-50/80 dark:from-[#070614] dark:via-[#0b0820] dark:to-[#0f0828]" />
      <div className="orb-a absolute top-[8%] left-[3%] w-72 h-72 rounded-full bg-indigo-300/20 dark:bg-indigo-600/18 blur-3xl pointer-events-none -z-10" />
      <div className="orb-b absolute top-[35%] right-[5%] w-96 h-96 rounded-full bg-violet-300/15 dark:bg-violet-700/14 blur-3xl pointer-events-none -z-10" />

      {/* Header */}
      <Header
        isRecording={isRecording}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        onToggleSummary={() => setSummaryOpen((v) => !v)}
        summaryOpen={summaryOpen}
      />

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Mobile sidebar overlay backdrop */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/40 backdrop-blur-[2px] lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar: fixed overlay on mobile, flex item on desktop */}
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
          />
        </aside>

        {/* Chat area */}
        <div className="flex-1 overflow-hidden min-w-0 flex flex-col">
          <ChatArea messages={activeMeeting.messages} isRecording={isRecording} />
        </div>

        {/* Summary panel — desktop side panel */}
        {summaryOpen && (
          <div className="hidden sm:flex w-64 flex-shrink-0">
            <SummaryPanel
              summary={activeMeeting.summary ?? []}
              onClose={() => setSummaryOpen(false)}
            />
          </div>
        )}
      </div>

      {/* Summary panel — mobile inline panel (between chat and controls) */}
      {summaryOpen && (
        <div
          className="sm:hidden flex-shrink-0 border-t border-slate-200/60 dark:border-indigo-500/10"
          style={{ maxHeight: '45vh' }}
        >
          <SummaryPanel
            summary={activeMeeting.summary ?? []}
            onClose={() => setSummaryOpen(false)}
          />
        </div>
      )}

      {/* Control panel */}
      <ControlPanel
        isRecording={isRecording}
        onToggleRecording={() => setIsRecording((v) => !v)}
        ttsInput={ttsInput}
        onTtsInputChange={setTtsInput}
        onSend={handleSend}
      />
    </main>
  )
}
