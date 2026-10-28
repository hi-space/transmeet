import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'
import {
  effectiveSourceLanguage,
  normalizeSpeechLanguages,
  resolveDirection,
} from '../src/lib/speechLanguages.ts'

test('saved legacy AUTO keeps opposite-language translation', () => {
  assert.deepEqual(normalizeSpeechLanguages({ sourceLang: 'auto', targetLang: 'ko' }), {
    sourceLang: 'auto',
    translationTarget: 'auto',
  })
})

test('saved fixed directions and new independent destinations survive migration', () => {
  for (const [saved, expected] of [
    [
      { sourceLang: 'ko', targetLang: 'en' },
      { sourceLang: 'ko', translationTarget: 'en' },
    ],
    [
      { sourceLang: 'en', targetLang: 'ko' },
      { sourceLang: 'en', translationTarget: 'ko' },
    ],
    [
      { sourceLang: 'auto', translationTarget: 'ko' },
      { sourceLang: 'auto', translationTarget: 'ko' },
    ],
    [
      { sourceLang: 'ko', translationTarget: 'auto' },
      { sourceLang: 'ko', translationTarget: 'auto' },
    ],
  ]) {
    assert.deepEqual(normalizeSpeechLanguages(saved), expected)
  }
})

test('missing or malformed saved language values have usable defaults', () => {
  for (const saved of [
    undefined,
    null,
    [],
    'auto',
    {},
    { sourceLang: 'ja', translationTarget: {} },
  ]) {
    assert.deepEqual(normalizeSpeechLanguages(saved), { sourceLang: 'en', translationTarget: 'ko' })
  }
})

test('Qwen uses automatic input without erasing another engines preferred input', () => {
  for (const preferred of ['auto', 'ko', 'en']) {
    assert.equal(effectiveSourceLanguage('qwen3-asr', preferred), 'auto')
    for (const provider of ['transcribe', 'whisper', 'nemotron']) {
      assert.equal(effectiveSourceLanguage(provider, preferred), preferred)
    }
  }
})

test('streaming Qwen honors Korean and English input selection while batch remains automatic', () => {
  for (const preferred of ['auto', 'ko', 'en']) {
    assert.equal(effectiveSourceLanguage('qwen3-asr', preferred, true), preferred)
    assert.equal(effectiveSourceLanguage('qwen3-asr', preferred, false), 'auto')
  }
})

test('actual detected language takes priority over the requested recognition language', () => {
  assert.deepEqual(resolveDirection('ko', 'auto', 'en'), { source: 'en', target: 'ko' })
  assert.deepEqual(resolveDirection('en', 'auto', 'ko'), { source: 'ko', target: 'en' })
})

test('fixed translation destinations stay fixed as the detected language changes', () => {
  for (const detected of ['ko', 'en']) {
    assert.deepEqual(resolveDirection('auto', 'ko', detected), { source: detected, target: 'ko' })
    assert.deepEqual(resolveDirection('auto', 'en', detected), { source: detected, target: 'en' })
  }
})

test('opposite-language translation follows Korean, English, then Korean speech', () => {
  assert.deepEqual(
    ['ko', 'en', 'ko'].map((language) => resolveDirection('auto', 'auto', language)),
    [
      { source: 'ko', target: 'en' },
      { source: 'en', target: 'ko' },
      { source: 'ko', target: 'en' },
    ]
  )
})

test('before detection, a fixed input is the fallback and AUTO falls back to English', () => {
  assert.deepEqual(resolveDirection('ko', 'auto'), { source: 'ko', target: 'en' })
  assert.deepEqual(resolveDirection('auto', 'auto'), { source: 'en', target: 'ko' })
})

test('reopened meetings only merge speech with the same source and destination languages', () => {
  const { toMeeting } = loadTypeScript('src/lib/api.ts', {
    '@/lib/cognito': { getIdToken: () => null },
  })
  const meeting = toMeeting({
    meetingId: 'languages',
    title: '한영 회의',
    createdAt: '2026-10-28T10:00:00Z',
    messages: [
      ['ko', 'en', '출시를 준비합니다.'],
      ['en', 'ko', 'The release is ready.'],
      ['en', 'en', 'Please review it.'],
      ['en', 'en', 'Thank you for reviewing.'],
    ].map(([detectedLanguage, targetLanguage, originalText], index) => ({
      id: `speech-${index}`,
      speaker: 'speaker1',
      detectedLanguage,
      targetLanguage,
      originalText,
      translatedText: `Result ${index}.`,
      timestamp: `2026-10-28T10:00:0${index}Z`,
    })),
  })
  assert.deepEqual(
    meeting.messages.map((message) => ({
      text: message.original,
      source: message.detectedLanguage,
      target: message.targetLanguage,
    })),
    [
      { text: '출시를 준비합니다.', source: 'ko', target: 'en' },
      { text: 'The release is ready.', source: 'en', target: 'ko' },
      { text: 'Please review it. Thank you for reviewing.', source: 'en', target: 'en' },
    ]
  )
})
