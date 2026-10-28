export const STT_PROVIDERS = [
  { id: 'transcribe', label: 'Transcribe' },
  { id: 'whisper', label: 'Whisper' },
  { id: 'qwen3-asr', label: 'Qwen3-ASR' },
  { id: 'nemotron', label: 'Nemotron 3.5' },
] as const

export type SttProvider = (typeof STT_PROVIDERS)[number]['id']

export interface SttStatus {
  provider: SttProvider
  status:
    | 'checking'
    | 'sleeping'
    | 'starting'
    | 'ready'
    | 'cooling'
    | 'unavailable'
    | 'error'
    | 'unknown'
  managed?: boolean
  instances?: number
  currentCopies?: number
  desiredCopies?: number
  message?: string
  recordingBlocked?: boolean
}

export function isManagedStt(provider: SttProvider): boolean {
  return provider === 'qwen3-asr' || provider === 'nemotron'
}

export function shouldStopSttRecording(provider: SttProvider, state?: SttStatus): boolean {
  if (!isManagedStt(provider) || !state || state.provider !== provider) return false
  if (state.recordingBlocked) return true
  return state.status !== 'ready' && state.status !== 'unknown' && state.status !== 'checking'
}

/** A saved provider selection never implies that its GPU is ready. */
export function sttRecordingAction(
  provider: SttProvider,
  state: SttStatus | undefined,
  isRecording: boolean
): 'start' | 'stop' | 'prepare' | 'wait' {
  if (isRecording) return 'stop'
  if (!isManagedStt(provider)) return 'start'
  if (!state || state.provider !== provider) return 'prepare'
  if (state.status === 'ready') return 'start'
  if (state.status === 'starting' || state.status === 'cooling') return 'wait'
  return 'prepare'
}

export function normalizeSttProvider(value: unknown): SttProvider {
  return STT_PROVIDERS.find((provider) => provider.id === value)?.id ?? 'transcribe'
}

export function sttChunkDuration(provider: SttProvider): number {
  return provider === 'transcribe' ? 700 : 2000
}
