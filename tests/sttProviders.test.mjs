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

test('batch models receive six seconds of context while Transcribe keeps streaming', () => {
  assert.equal(sttChunkDuration('transcribe'), 700)
  assert.equal(sttChunkDuration('whisper'), 6000)
  assert.equal(sttChunkDuration('qwen3-asr'), 6000)
  assert.equal(sttChunkDuration('nemotron'), 6000)
})

test('selected audio windows apply to batch engines without slowing the continuous stream', () => {
  for (const [seconds, milliseconds] of [[2, 2000], [4, 4000], [6, 6000], [8, 8000], [10, 10000]]) {
    for (const provider of ['whisper', 'qwen3-asr', 'nemotron']) {
      assert.equal(sttChunkDuration(provider, seconds), milliseconds)
    }
    assert.equal(sttChunkDuration('transcribe', seconds), 700)
  }
})

test('missing or corrupt saved audio windows use the six-second default', () => {
  for (const value of [undefined, null, '8', 0, -2, 3, 12, NaN, Infinity, {}, []]) {
    assert.equal(sttChunkDuration('whisper', value), 6000)
  }
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
