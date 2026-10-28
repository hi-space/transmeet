import assert from 'node:assert/strict'
import { test } from 'node:test'
import { normalizeSttProvider, sttChunkDuration } from '../src/lib/sttProviders.ts'

test('all four saved speech engines remain selectable', () => {
  for (const provider of ['transcribe', 'whisper', 'qwen3-asr', 'nemotron']) {
    assert.equal(normalizeSttProvider(provider), provider)
  }
})

test('malformed saved engines fall back to Transcribe', () => {
  for (const value of [undefined, null, '', 'qwen', {}, 12, '__proto__']) {
    assert.equal(normalizeSttProvider(value), 'transcribe')
  }
})

test('buffered models receive enough audio context while Transcribe keeps streaming', () => {
  assert.equal(sttChunkDuration('transcribe'), 700)
  assert.equal(sttChunkDuration('whisper'), 2000)
  assert.equal(sttChunkDuration('qwen3-asr'), 2000)
  assert.equal(sttChunkDuration('nemotron'), 2000)
})
