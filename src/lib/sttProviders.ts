export const STT_PROVIDERS = [
  { id: 'transcribe', label: 'Transcribe' },
  { id: 'whisper', label: 'Whisper' },
  { id: 'qwen3-asr', label: 'Qwen3-ASR' },
  { id: 'nemotron', label: 'Nemotron 3.5' },
] as const

export type SttProvider = (typeof STT_PROVIDERS)[number]['id']

export const STT_CHUNK_SECONDS = [2, 4, 6, 8, 10] as const
export const DEFAULT_STT_CHUNK_SECONDS = 6
export type SttChunkSeconds = (typeof STT_CHUNK_SECONDS)[number]

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
  /** Omitted by older backends, which still expect complete audio windows. */
  streaming?: boolean
}

export function isManagedStt(provider: SttProvider, state?: SttStatus): boolean {
  return (
    provider === 'qwen3-asr' ||
    provider === 'nemotron' ||
    (provider === 'whisper' && state?.provider === provider && state.streaming === true)
  )
}

export function isStreamingStt(provider: SttProvider, state?: SttStatus): boolean {
  return (
    provider === 'transcribe' ||
    (isManagedStt(provider, state) && state?.provider === provider && state.streaming === true)
  )
}

/** SageMaker models revise hypotheses; translation consumes their final utterances. */
export function shouldTranslateSttPartial(provider: SttProvider): boolean {
  return provider === 'transcribe'
}

interface SttUtteranceIdentity {
  sttProvider?: SttProvider
  streamId?: string
  utteranceId?: number
}

export function matchesSttUtterance(
  result: SttUtteranceIdentity,
  pending: SttUtteranceIdentity | null | undefined
): boolean {
  return (
    (!result.sttProvider || !pending?.sttProvider || result.sttProvider === pending.sttProvider) &&
    (result.streamId === undefined ||
      result.utteranceId === undefined ||
      pending?.streamId === undefined ||
      pending.utteranceId === undefined ||
      (result.streamId === pending.streamId && result.utteranceId === pending.utteranceId))
  )
}

export function shouldStopSttRecording(provider: SttProvider, state?: SttStatus): boolean {
  if (!isManagedStt(provider, state) || !state || state.provider !== provider) return false
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
  // Discover the wire protocol before microphone capture chooses its frame size.
  // Legacy Whisper answers ready without streaming and never needs preparation.
  if (
    provider === 'whisper' &&
    (!state || state.provider !== provider || state.status === 'checking')
  ) {
    return 'wait'
  }
  if (!isManagedStt(provider, state)) return 'start'
  if (!state || state.provider !== provider) return 'prepare'
  if (state.status === 'ready') return 'start'
  if (state.status === 'starting' || state.status === 'cooling') return 'wait'
  return 'prepare'
}

export function normalizeSttProvider(value: unknown): SttProvider {
  return STT_PROVIDERS.find((provider) => provider.id === value)?.id ?? 'transcribe'
}

export function normalizeSttChunkSeconds(value: unknown): SttChunkSeconds {
  return STT_CHUNK_SECONDS.find((seconds) => seconds === value) ?? DEFAULT_STT_CHUNK_SECONDS
}

export function sttChunkDuration(
  provider: SttProvider,
  seconds: SttChunkSeconds = DEFAULT_STT_CHUNK_SECONDS,
  state?: SttStatus
): number {
  if (provider === 'transcribe') return 700
  if (isStreamingStt(provider, state)) return 160
  return normalizeSttChunkSeconds(seconds) * 1000
}
