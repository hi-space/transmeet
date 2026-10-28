import assert from 'node:assert/strict'
import { test } from 'node:test'
import { normalizeSttProvider, sttChunkDuration, sttRecordingAction, shouldStopSttRecording } from '../src/lib/sttProviders.ts'

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

test('a sleeping or unknown managed engine prepares before capturing audio', () => {
  assert.equal(sttRecordingAction('qwen3-asr', undefined, false), 'prepare')
  assert.equal(sttRecordingAction('nemotron', { provider: 'nemotron', status: 'sleeping' }, false), 'prepare')
  assert.equal(sttRecordingAction('nemotron', { provider: 'nemotron', status: 'starting' }, false), 'wait')
})

test('only readiness for the selected engine permits microphone capture', () => {
  assert.equal(sttRecordingAction('qwen3-asr', { provider: 'nemotron', status: 'ready' }, false), 'prepare')
  assert.equal(sttRecordingAction('qwen3-asr', { provider: 'qwen3-asr', status: 'ready' }, false), 'start')
  assert.equal(sttRecordingAction('transcribe', undefined, false), 'start')
  assert.equal(sttRecordingAction('whisper', undefined, false), 'start')
})

test('recording can always be stopped even when the engine becomes unavailable', () => {
  assert.equal(sttRecordingAction('nemotron', { provider: 'nemotron', status: 'error' }, true), 'stop')
})

test('a transient status-query failure does not interrupt an established recording', () => {
  assert.equal(shouldStopSttRecording('nemotron', { provider: 'nemotron', status: 'unknown' }), false)
  assert.equal(shouldStopSttRecording('nemotron', { provider: 'nemotron', status: 'sleeping' }), true)
  assert.equal(shouldStopSttRecording('nemotron', {
    provider: 'nemotron', status: 'unknown', recordingBlocked: true,
  }), true)
})
