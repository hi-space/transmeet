import type { SttProvider } from './sttProviders'

export type Language = 'ko' | 'en'
export type SourceLanguage = Language | 'auto'
export type TranslationTarget = Language | 'auto'

export interface SpeechLanguages {
  sourceLang: SourceLanguage
  translationTarget: TranslationTarget
}

function isLanguage(value: unknown): value is Language {
  return value === 'ko' || value === 'en'
}

/** Old AUTO settings ignored targetLang; preserve that behavior during migration. */
export function normalizeSpeechLanguages(value: unknown): SpeechLanguages {
  const saved =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {}
  const sourceLang =
    saved.sourceLang === 'auto' || isLanguage(saved.sourceLang) ? saved.sourceLang : 'en'
  const translationTarget =
    saved.translationTarget === 'auto' || isLanguage(saved.translationTarget)
      ? saved.translationTarget
      : sourceLang === 'auto'
        ? 'auto'
        : isLanguage(saved.targetLang)
          ? saved.targetLang
          : 'ko'
  return { sourceLang, translationTarget }
}

/** Qwen's batch chat adapter is automatic; its streaming API accepts a language. */
export function effectiveSourceLanguage(
  provider: SttProvider,
  preferred: SourceLanguage,
  streaming = false
): SourceLanguage {
  return provider === 'qwen3-asr' && !streaming ? 'auto' : preferred
}

/**
 * Resolve a concrete translation direction independently of the recognition mode.
 * Detected speech takes precedence over the preference. Before detection, use a
 * fixed input preference, or English for AUTO. An AUTO destination is the opposite.
 */
export function resolveDirection(
  sourceLang: SourceLanguage,
  translationTarget: TranslationTarget,
  detected?: Language | null
): { source: Language; target: Language } {
  const source = detected ?? (sourceLang === 'auto' ? 'en' : sourceLang)
  const target = translationTarget === 'auto' ? (source === 'ko' ? 'en' : 'ko') : translationTarget
  return { source, target }
}
