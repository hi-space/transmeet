'use client'

import { useState } from 'react'

type RecordingState = 'idle' | 'recording'

const WAVE_DELAYS = [0, 0.12, 0.24, 0.36, 0.24, 0.12, 0]
const WAVE_HEIGHTS = ['60%', '80%', '90%', '100%', '90%', '80%', '60%']

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
    <div className="glass-footer relative z-20 px-4 pt-3 pb-5">
      {/* Gradient accent line at top */}
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 dark:via-indigo-500/25 to-transparent" />

      <div className="flex items-center justify-between gap-4 max-w-sm mx-auto">
        {/* Summarize button */}
        <SideButton
          onClick={handleSummarize}
          disabled={!isRecording}
          label="요약"
          icon={
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-5 h-5"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" x2="8" y1="13" y2="13" />
              <line x1="16" x2="8" y1="17" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
          }
        />

        {/* Center: Waveform + Record button + status */}
        <div className="flex flex-col items-center gap-2">
          {/* Waveform visualizer */}
          <div className="h-8 flex items-center justify-center gap-[3px] w-20">
            {isRecording ? (
              WAVE_DELAYS.map((delay, i) => (
                <div
                  key={i}
                  className="wave-bar w-[3px] rounded-full bg-gradient-to-t from-rose-500 to-rose-300"
                  style={{
                    height: WAVE_HEIGHTS[i],
                    animationDelay: `${delay}s`,
                  }}
                />
              ))
            ) : (
              <div className="flex items-center gap-[3px]">
                {WAVE_HEIGHTS.map((h, i) => (
                  <div
                    key={i}
                    className="w-[3px] rounded-full bg-slate-200 dark:bg-slate-700"
                    style={{ height: WAVE_HEIGHTS[i] }}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Record button */}
          <div className="relative flex items-center justify-center">
            {/* Pulse rings */}
            {isRecording && (
              <>
                <span className="pulse-ring-1 absolute inset-0 rounded-full bg-rose-500/30" />
                <span className="pulse-ring-2 absolute inset-0 rounded-full bg-rose-500/20" />
                <span className="pulse-ring-3 absolute inset-0 rounded-full bg-rose-500/10" />
              </>
            )}
            <button
              onClick={toggleRecording}
              aria-label={isRecording ? '녹음 중지' : '녹음 시작'}
              className={`
                relative w-16 h-16 rounded-full flex items-center justify-center
                transition-all duration-300 shadow-xl
                ${
                  isRecording
                    ? 'bg-gradient-to-br from-rose-500 to-red-600 shadow-rose-500/40 scale-105'
                    : 'bg-gradient-to-br from-indigo-500 to-violet-600 shadow-indigo-500/40 hover:scale-105 active:scale-95'
                }
              `}
            >
              {/* Inner glow ring */}
              <div
                className={`absolute inset-1 rounded-full border ${isRecording ? 'border-white/20' : 'border-white/15'}`}
              />
              {isRecording ? (
                <svg viewBox="0 0 24 24" fill="white" className="w-6 h-6">
                  <rect x="7" y="7" width="10" height="10" rx="2" />
                </svg>
              ) : (
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="white"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-6 h-6"
                >
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                </svg>
              )}
            </button>
          </div>

          {/* Status label */}
          <div className="h-4 flex items-center">
            {isRecording ? (
              <div className="flex items-center gap-1.5 slide-up-fade">
                <span className="blink-dot w-1.5 h-1.5 rounded-full bg-rose-500" />
                <span className="text-[11px] font-semibold text-rose-500 tracking-wide">
                  녹음 중
                </span>
              </div>
            ) : (
              <span className="text-[11px] text-slate-400 dark:text-slate-500">탭하여 시작</span>
            )}
          </div>
        </div>

        {/* TTS button */}
        <SideButton
          onClick={handleTTS}
          disabled={false}
          label="TTS"
          icon={
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-5 h-5"
            >
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
            </svg>
          }
        />
      </div>
    </div>
  )
}

function SideButton({
  onClick,
  disabled,
  label,
  icon,
}: {
  onClick: () => void
  disabled: boolean
  label: string
  icon: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        flex flex-col items-center gap-1.5 w-14 py-2.5 rounded-2xl
        border transition-all duration-200 select-none
        ${
          disabled
            ? 'opacity-35 cursor-not-allowed border-slate-200/60 dark:border-white/5 text-slate-400 dark:text-slate-600'
            : 'border-slate-200/70 dark:border-white/8 text-slate-500 dark:text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:border-indigo-200 dark:hover:border-indigo-500/25 hover:bg-indigo-50/60 dark:hover:bg-indigo-500/8 active:scale-95'
        }
      `}
      aria-label={label}
    >
      {icon}
      <span className="text-[10px] font-semibold tracking-wide">{label}</span>
    </button>
  )
}
