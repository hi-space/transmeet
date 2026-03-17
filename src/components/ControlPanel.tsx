'use client'

const WAVE_DELAYS = [0, 0.12, 0.24, 0.36, 0.24, 0.12, 0]
const WAVE_HEIGHTS = ['60%', '80%', '90%', '100%', '90%', '80%', '60%']

interface Props {
  isRecording: boolean
  onToggleRecording: () => void
  ttsInput: string
  onTtsInputChange: (val: string) => void
  onSend: () => void
  onStopTts: () => void
  audioLevel?: number // 0–1, real-time mic amplitude
  isTtsPending?: boolean
}

export default function ControlPanel({
  isRecording,
  onToggleRecording,
  ttsInput,
  onTtsInputChange,
  onSend,
  onStopTts,
  audioLevel = 0,
  isTtsPending = false,
}: Props) {
  return (
    <div className="glass-footer relative z-20 px-4 pt-2 pb-4 flex-shrink-0">
      <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 dark:via-indigo-500/25 to-transparent" />

      {/* Waveform — visible while recording, scales with real audio level */}
      {isRecording && (
        <div
          className="flex items-center justify-center gap-[3px] h-5 mb-2 transition-transform duration-75"
          style={{ transform: `scaleY(${0.25 + audioLevel * 0.75})` }}
        >
          {WAVE_DELAYS.map((delay, i) => (
            <div
              key={i}
              className="wave-bar w-[2px] rounded-full bg-gradient-to-t from-rose-500 to-rose-300"
              style={{ height: WAVE_HEIGHTS[i], animationDelay: `${delay}s` }}
            />
          ))}
        </div>
      )}

      <div className="flex items-center gap-2.5">
        {/* Record button */}
        <div className="relative flex-shrink-0">
          {isRecording && (
            <>
              <span className="pulse-ring-1 absolute inset-0 rounded-full bg-rose-500/30" />
              <span className="pulse-ring-2 absolute inset-0 rounded-full bg-rose-500/20" />
            </>
          )}
          <button
            onClick={onToggleRecording}
            aria-label={isRecording ? '녹음 중지' : '녹음 시작'}
            className={`
              relative w-11 h-11 rounded-full flex items-center justify-center
              transition-all duration-300 shadow-lg
              ${
                isRecording
                  ? 'bg-gradient-to-br from-rose-500 to-red-600 shadow-rose-500/40 scale-105'
                  : 'bg-gradient-to-br from-indigo-500 to-violet-600 shadow-indigo-500/30 hover:scale-105 active:scale-95'
              }
            `}
          >
            <div
              className={`absolute inset-0.5 rounded-full border ${
                isRecording ? 'border-white/20' : 'border-white/15'
              }`}
            />
            {isRecording ? (
              <svg viewBox="0 0 24 24" fill="white" className="w-4 h-4">
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
                className="w-[18px] h-[18px]"
              >
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" x2="12" y1="19" y2="22" />
              </svg>
            )}
          </button>
        </div>

        {/* TTS stop button — visible only while TTS is playing */}
        {isTtsPending && (
          <button
            onClick={onStopTts}
            aria-label="TTS 중지"
            className="flex-shrink-0 w-9 h-9 rounded-xl flex items-center justify-center bg-rose-50 dark:bg-rose-900/20 border border-rose-200/60 dark:border-rose-500/20 text-rose-500 hover:bg-rose-100 dark:hover:bg-rose-900/30 active:scale-95 transition-all"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
              <rect x="5" y="5" width="14" height="14" rx="2" />
            </svg>
          </button>
        )}

        {/* KO → EN text input */}
        <input
          type="text"
          value={ttsInput}
          onChange={(e) => onTtsInputChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
          disabled={isTtsPending}
          placeholder="한글로 입력하면 영어로 번역됩니다..."
          className="flex-1 min-w-0 px-3.5 py-2.5 rounded-2xl bg-white/70 dark:bg-white/5 border border-slate-200/70 dark:border-white/10 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-400/50 focus:border-indigo-300/70 dark:focus:border-indigo-500/40 backdrop-blur-sm transition-colors disabled:opacity-60"
        />

        {/* Send button */}
        <button
          onClick={onSend}
          disabled={!ttsInput.trim() || isTtsPending}
          aria-label="전송"
          className="flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-md shadow-indigo-500/20 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none hover:opacity-90 active:scale-95 transition-all"
        >
          {isTtsPending ? (
            <svg
              className="w-4 h-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
            </svg>
          ) : (
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-4 h-4"
            >
              <path d="m22 2-7 20-4-9-9-4 20-7Z" />
              <path d="M22 2 11 13" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}
