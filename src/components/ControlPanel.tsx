'use client'

import { useState } from 'react'

type RecordingState = 'idle' | 'recording'

export default function ControlPanel() {
  const [recordingState, setRecordingState] = useState<RecordingState>('idle')

  const toggleRecording = () => {
    setRecordingState((prev) => (prev === 'idle' ? 'recording' : 'idle'))
  }

  const handleSummarize = () => {
    // TODO: Implement summarize via Bedrock Claude
  }

  const handleTTS = () => {
    // TODO: Implement TTS via Amazon Polly
  }

  const isRecording = recordingState === 'recording'

  return (
    <div className="border-t border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-4 safe-area-pb">
      <div className="flex items-center justify-between gap-3 max-w-lg mx-auto">
        {/* Summarize button */}
        <button
          onClick={handleSummarize}
          disabled={!isRecording}
          className="flex flex-col items-center gap-1 px-4 py-2 rounded-xl text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          aria-label="요약"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-6 h-6"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" x2="8" y1="13" y2="13" />
            <line x1="16" x2="8" y1="17" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
          <span className="text-xs font-medium">요약</span>
        </button>

        {/* Main record button */}
        <button
          onClick={toggleRecording}
          className={`
            relative w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-lg
            ${
              isRecording
                ? 'bg-red-500 hover:bg-red-600 shadow-red-500/30'
                : 'bg-brand-600 hover:bg-brand-700 shadow-brand-600/30'
            }
          `}
          aria-label={isRecording ? '녹음 중지' : '녹음 시작'}
        >
          {isRecording && (
            <span className="absolute inset-0 rounded-full bg-red-500 animate-ping opacity-30" />
          )}
          {isRecording ? (
            <svg viewBox="0 0 24 24" fill="white" className="w-7 h-7">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          ) : (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-7 h-7"
            >
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" x2="12" y1="19" y2="22" />
            </svg>
          )}
        </button>

        {/* TTS button */}
        <button
          onClick={handleTTS}
          className="flex flex-col items-center gap-1 px-4 py-2 rounded-xl text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-all"
          aria-label="음성 출력"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="w-6 h-6"
          >
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
          </svg>
          <span className="text-xs font-medium">TTS</span>
        </button>
      </div>

      {isRecording && (
        <p className="text-center text-xs text-red-500 mt-2 animate-pulse">녹음 중...</p>
      )}
    </div>
  )
}
