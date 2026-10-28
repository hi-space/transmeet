import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  normalizeSttProvider,
  sttChunkDuration,
  sttRecordingAction,
  shouldStopSttRecording,
  isManagedStt,
  isStreamingStt,
  shouldTranslateSttPartial,
  matchesSttUtterance,
} from '../src/lib/sttProviders.ts'

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
  for (const [seconds, milliseconds] of [
    [2, 2000],
    [4, 4000],
    [6, 6000],
    [8, 8000],
    [10, 10000],
  ]) {
    for (const provider of ['whisper', 'qwen3-asr', 'nemotron']) {
      assert.equal(sttChunkDuration(provider, seconds), milliseconds)
    }
    assert.equal(sttChunkDuration('transcribe', seconds), 700)
  }
})

test('enabled Nemotron streaming sends audio without waiting for the saved batch window', () => {
  const status = { provider: 'nemotron', status: 'ready', streaming: true }
  for (const seconds of [2, 4, 6, 8, 10, undefined]) {
    assert.equal(sttChunkDuration('nemotron', seconds, status), 160)
  }
})

test('advertised Qwen streaming uses short audio frames with every saved window', () => {
  const status = { provider: 'qwen3-asr', status: 'ready', streaming: true }
  assert.equal(isStreamingStt('qwen3-asr', status), true)
  for (const seconds of [2, 4, 6, 8, 10, undefined]) {
    assert.equal(sttChunkDuration('qwen3-asr', seconds, status), 160)
  }
})

test('advertised Whisper streaming uses short frames and final-only translation', () => {
  const status = { provider: 'whisper', status: 'ready', streaming: true }
  assert.equal(isStreamingStt('whisper', status), true)
  assert.equal(isManagedStt('whisper', status), true)
  assert.equal(shouldTranslateSttPartial('whisper'), false)
  for (const seconds of [2, 4, 6, 8, 10, undefined]) {
    assert.equal(sttChunkDuration('whisper', seconds, status), 160)
  }
})

test('Whisper waits for its first capability response before choosing audio capture', () => {
  for (const status of [
    undefined,
    { provider: 'whisper', status: 'checking' },
    { provider: 'nemotron', status: 'ready', streaming: true },
  ]) {
    assert.equal(sttRecordingAction('whisper', status, false), 'wait')
    assert.equal(sttRecordingAction('whisper', status, true), 'stop')
  }
})

test('old and disabled Whisper capabilities retain batch capture without preparation', () => {
  for (const status of [
    { provider: 'whisper', status: 'ready' },
    { provider: 'whisper', status: 'ready', streaming: false },
  ]) {
    assert.equal(isManagedStt('whisper', status), false)
    assert.equal(isStreamingStt('whisper', status), false)
    assert.equal(sttChunkDuration('whisper', 8, status), 8000)
    assert.equal(sttRecordingAction('whisper', status, false), 'start')
    assert.equal(shouldStopSttRecording('whisper', status), false)
  }
  assert.equal(isManagedStt('whisper'), false)
})

test('advertised Whisper requires readiness and stops on a confirmed stream failure', () => {
  const status = { provider: 'whisper', status: 'ready', streaming: true }
  assert.equal(sttRecordingAction('whisper', status, false), 'start')
  assert.equal(sttRecordingAction('whisper', { ...status, status: 'starting' }, false), 'wait')
  assert.equal(sttRecordingAction('whisper', { ...status, status: 'error' }, false), 'prepare')
  assert.equal(shouldStopSttRecording('whisper', { ...status, status: 'error' }), true)
  assert.equal(shouldStopSttRecording('whisper', { ...status, status: 'unknown' }), false)
  assert.equal(
    shouldStopSttRecording('whisper', { ...status, status: 'unknown', recordingBlocked: true }),
    true
  )
})

test('a Whisper final cannot clear a newer utterance or another recording', () => {
  const pending = { sttProvider: 'whisper', streamId: 'recording:1', utteranceId: 2 }
  assert.equal(matchesSttUtterance({ ...pending }, pending), true)
  assert.equal(matchesSttUtterance({ ...pending, utteranceId: 1 }, pending), false)
  assert.equal(matchesSttUtterance({ ...pending, streamId: 'previous:1' }, pending), false)
  assert.equal(matchesSttUtterance({ ...pending, sttProvider: 'qwen3-asr' }, pending), false)
})

test('old or disabled Qwen backends retain batch capture', () => {
  for (const status of [
    undefined,
    { provider: 'qwen3-asr', status: 'ready' },
    { provider: 'qwen3-asr', status: 'ready', streaming: false },
    { provider: 'nemotron', status: 'ready', streaming: true },
  ]) {
    assert.equal(isStreamingStt('qwen3-asr', status), false)
    assert.equal(sttChunkDuration('qwen3-asr', 8, status), 8000)
  }
})

test('Qwen and Nemotron hypotheses are translated only after finalization', () => {
  assert.equal(shouldTranslateSttPartial('qwen3-asr'), false)
  assert.equal(shouldTranslateSttPartial('nemotron'), false)
  assert.equal(shouldTranslateSttPartial('transcribe'), true)
})

test('an old Qwen final cannot clear the next utterance or another recording', () => {
  const pending = { sttProvider: 'qwen3-asr', streamId: 'recording:1', utteranceId: 2 }
  assert.equal(matchesSttUtterance({ ...pending }, pending), true)
  assert.equal(matchesSttUtterance({ ...pending, utteranceId: 1 }, pending), false)
  assert.equal(matchesSttUtterance({ ...pending, streamId: 'old:1' }, pending), false)
  assert.equal(matchesSttUtterance({ ...pending, sttProvider: 'nemotron' }, pending), false)
  // Transcribe and older servers do not attach per-utterance IDs.
  assert.equal(matchesSttUtterance({}, null), true)
})

test('old or disabled Nemotron backends retain the selected batch window', () => {
  for (const status of [
    undefined,
    { provider: 'nemotron', status: 'ready' },
    { provider: 'nemotron', status: 'ready', streaming: false },
    { provider: 'qwen3-asr', status: 'ready', streaming: true },
  ]) {
    assert.equal(sttChunkDuration('nemotron', 8, status), 8000)
    assert.equal(sttChunkDuration('nemotron', undefined, status), 6000)
  }
})

test('Nemotron readiness cannot change another engines capture interval', () => {
  const status = { provider: 'nemotron', status: 'ready', streaming: true }
  assert.equal(sttChunkDuration('transcribe', 10, status), 700)
  assert.equal(sttChunkDuration('whisper', 4, status), 4000)
  assert.equal(sttChunkDuration('qwen3-asr', 8, status), 8000)
})

test('missing or corrupt saved audio windows use the six-second default', () => {
  for (const value of [undefined, null, '8', 0, -2, 3, 12, NaN, Infinity, {}, []]) {
    assert.equal(sttChunkDuration('whisper', value), 6000)
  }
})

test('a sleeping or unknown managed engine prepares before capturing audio', () => {
  assert.equal(sttRecordingAction('qwen3-asr', undefined, false), 'prepare')
  assert.equal(
    sttRecordingAction('nemotron', { provider: 'nemotron', status: 'sleeping' }, false),
    'prepare'
  )
  assert.equal(
    sttRecordingAction('nemotron', { provider: 'nemotron', status: 'starting' }, false),
    'wait'
  )
})

test('only readiness for the selected engine permits microphone capture', () => {
  assert.equal(
    sttRecordingAction('qwen3-asr', { provider: 'nemotron', status: 'ready' }, false),
    'prepare'
  )
  assert.equal(
    sttRecordingAction('qwen3-asr', { provider: 'qwen3-asr', status: 'ready' }, false),
    'start'
  )
  assert.equal(sttRecordingAction('transcribe', undefined, false), 'start')
  assert.equal(
    sttRecordingAction('whisper', { provider: 'whisper', status: 'ready' }, false),
    'start'
  )
})

test('recording can always be stopped even when the engine becomes unavailable', () => {
  assert.equal(
    sttRecordingAction('nemotron', { provider: 'nemotron', status: 'error' }, true),
    'stop'
  )
})

test('a transient status-query failure does not interrupt an established recording', () => {
  assert.equal(
    shouldStopSttRecording('nemotron', { provider: 'nemotron', status: 'unknown' }),
    false
  )
  assert.equal(
    shouldStopSttRecording('nemotron', { provider: 'nemotron', status: 'sleeping' }),
    true
  )
  assert.equal(
    shouldStopSttRecording('nemotron', {
      provider: 'nemotron',
      status: 'unknown',
      recordingBlocked: true,
    }),
    true
  )
})
