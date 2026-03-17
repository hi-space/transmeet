'use client'

import { useState, useEffect } from 'react'
import type { WsStatus } from '@/lib/websocket'

interface Props {
  isRecording: boolean
  wsStatus?: WsStatus
  onToggleSidebar: () => void
  onToggleSummary: () => void
  summaryOpen: boolean
}

export default function Header({
  isRecording,
  wsStatus,
  onToggleSidebar,
  onToggleSummary,
  summaryOpen,
}: Props) {
  const [isDark, setIsDark] = useState(false)

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains('dark'))
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains('dark'))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  return (
    <header className="glass-header relative z-50 h-12 flex items-center px-4 flex-shrink-0">
      <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 dark:via-indigo-500/25 to-transparent" />

      <div className="flex items-center justify-between w-full">
        {/* Left: hamburger (mobile) + logo */}
        <div className="flex items-center gap-1.5">
          {/* Hamburger - mobile only */}
          <button
            onClick={onToggleSidebar}
            className="lg:hidden w-8 h-8 flex items-center justify-center rounded-lg text-slate-500 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-500/10 transition-all"
            aria-label="메뉴"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="w-5 h-5"
            >
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="4" y1="12" x2="20" y2="12" />
              <line x1="4" y1="18" x2="20" y2="18" />
            </svg>
          </button>

          {/* Logo */}
          <div className="flex items-center gap-2">
            <div className="relative w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-md flex-shrink-0">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="white"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="w-[15px] h-[15px]"
              >
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            </div>
            <span className="text-sm font-bold bg-gradient-to-r from-indigo-600 to-violet-600 dark:from-indigo-400 dark:to-violet-400 bg-clip-text text-transparent">
              TransMeet
            </span>
          </div>
        </div>

        {/* Right: recording status + summary toggle + theme toggle */}
        <div className="flex items-center gap-1">
          {/* Recording indicator */}
          {isRecording && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-rose-50 dark:bg-rose-900/20 border border-rose-200/60 dark:border-rose-500/20 mr-1">
              <span className="blink-dot w-1.5 h-1.5 rounded-full bg-rose-500" />
              <span className="text-[10px] font-semibold text-rose-500 tracking-wide">녹음 중</span>
            </div>
          )}

          {/* WebSocket status — shown when connecting or errored */}
          {!isRecording && wsStatus === 'connecting' && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-amber-50 dark:bg-amber-900/20 border border-amber-200/60 dark:border-amber-500/20 mr-1">
              <span className="blink-dot w-1.5 h-1.5 rounded-full bg-amber-500" />
              <span className="text-[10px] font-semibold text-amber-600 dark:text-amber-400 tracking-wide">
                연결 중
              </span>
            </div>
          )}
          {wsStatus === 'error' && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-red-50 dark:bg-red-900/20 border border-red-200/60 dark:border-red-500/20 mr-1">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
              <span className="text-[10px] font-semibold text-red-500 tracking-wide">
                연결 오류
              </span>
            </div>
          )}

          {/* Summary toggle */}
          <button
            onClick={onToggleSummary}
            aria-label="요약 보기"
            className={`w-8 h-8 flex items-center justify-center rounded-lg transition-all duration-200 ${
              summaryOpen
                ? 'text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-500/15'
                : 'text-slate-500 dark:text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-500/10'
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="w-[18px] h-[18px]"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" x2="8" y1="13" y2="13" />
              <line x1="16" x2="8" y1="17" y2="17" />
            </svg>
          </button>

          {/* Theme toggle */}
          <button
            onClick={() => document.documentElement.classList.toggle('dark')}
            aria-label="테마 전환"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-500 hover:text-indigo-600 dark:text-slate-400 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-500/10 transition-all duration-200"
          >
            {isDark ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-[18px] h-[18px]"
              >
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
              </svg>
            ) : (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-[18px] h-[18px]"
              >
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </header>
  )
}
