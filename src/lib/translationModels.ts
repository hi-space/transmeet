// Global inference profiles verified in us-east-1 on 2026-10-28.
export const TRANSLATION_MODELS = [
  { id: 'global.anthropic.claude-haiku-5-5', label: 'Haiku 5.5' },
  { id: 'global.openai.gpt-6-luna', label: 'GPT-6 Luna' },
] as const

export type TranslationModelId = (typeof TRANSLATION_MODELS)[number]['id']

export const DEFAULT_TRANSLATION_MODEL_ID: TranslationModelId = TRANSLATION_MODELS[0].id

/** Retired or corrupt saved choices must resolve to a model shown in settings. */
export function normalizeTranslationModel(value: unknown): TranslationModelId {
  return TRANSLATION_MODELS.find((model) => model.id === value)?.id ?? DEFAULT_TRANSLATION_MODEL_ID
}
