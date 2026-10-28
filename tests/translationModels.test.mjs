import assert from 'node:assert/strict'
import { test } from 'node:test'
import { normalizeTranslationModel } from '../src/lib/translationModels.ts'

test('preserves either supported global profile when loading saved settings', () => {
  for (const model of ['global.anthropic.claude-haiku-5-5', 'global.openai.gpt-6-luna']) {
    assert.equal(normalizeTranslationModel(model), model)
  }
})

test('moves retired translation choices to the current default', () => {
  for (const model of [
    'us.amazon.nova-micro-v1:0',
    'global.amazon.nova-2-lite-v1:0',
    'global.anthropic.claude-haiku-4-5-20251001-v1:0',
  ]) {
    assert.equal(normalizeTranslationModel(model), 'global.anthropic.claude-haiku-5-5')
  }
})

test('rejects malformed and non-global model identifiers', () => {
  for (const model of [null, undefined, '', 12, {}, [], '__proto__', 'openai.gpt-6-luna']) {
    assert.equal(normalizeTranslationModel(model), 'global.anthropic.claude-haiku-5-5')
  }
})
