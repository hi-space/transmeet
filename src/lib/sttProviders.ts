export const STT_PROVIDERS = [
  { id: 'transcribe', label: 'Transcribe' },
  { id: 'whisper', label: 'Whisper' },
  { id: 'qwen3-asr', label: 'Qwen3-ASR' },
  { id: 'nemotron', label: 'Nemotron 3.5' },
] as const

export type SttProvider = (typeof STT_PROVIDERS)[number]['id']

export function normalizeSttProvider(value: unknown): SttProvider {
  return STT_PROVIDERS.find((provider) => provider.id === value)?.id ?? 'transcribe'
}

export function sttChunkDuration(provider: SttProvider): number {
  return provider === 'transcribe' ? 700 : 2000
}
