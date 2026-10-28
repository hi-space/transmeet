import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const root = fileURLToPath(new URL('../', import.meta.url))

// 기존 Node 테스트 러너에서 실제 TS/TSX 모듈과 @/ 경로를 실행한다.
function loadSource(relativePath) {
  const filename = path.join(root, relativePath)
  const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  })
  const localRequire = (specifier) => {
    if (specifier === '@/lib/cognito') {
      return {
        getIdToken: () => {
          throw new Error('These tests must not call authenticated APIs')
        },
      }
    }
    if (specifier.startsWith('@/')) {
      const target = `src/${specifier.slice(2)}`
      return loadSource(`${target}${existsSync(path.join(root, `${target}.ts`)) ? '.ts' : '.tsx'}`)
    }
    return require(specifier)
  }
  const exports = {}
  new Function('exports', 'require', outputText)(exports, localRequire)
  return exports
}

function message(speaker, index = 0) {
  return {
    id: `message-${index}`,
    speaker,
    original: `Utterance ${index}.`,
    translation: '',
    timestamp: `2026-10-28T10:00:0${index}.000Z`,
  }
}

test('loading a meeting preserves distinct speakers beyond two without merging their turns', () => {
  const { toMeeting } = loadSource('src/lib/api.ts')
  const speakers = ['speaker1', 'speaker3', 'speaker11', 'speaker3', 'me']
  const meeting = toMeeting({
    meetingId: 'meeting-1',
    title: 'Speaker test',
    createdAt: '2026-10-28T10:00:00.000Z',
    messages: speakers.map((speaker, index) => {
      const msg = message(speaker, index)
      return { ...msg, originalText: msg.original, translatedText: msg.translation }
    }),
  })

  assert.deepEqual(
    meeting.messages.map((msg) => msg.speaker),
    speakers
  )
  assert.deepEqual(
    meeting.messages.map((msg) => msg.original),
    speakers.map((_, i) => `Utterance ${i}.`)
  )
})

test('invalid saved speaker IDs still use the default speaker', () => {
  const { toMeeting } = loadSource('src/lib/api.ts')
  for (const speaker of [
    'speaker0',
    'speaker-1',
    'speaker1.5',
    'speaker01',
    'unknown',
    'constructor',
  ]) {
    const meeting = toMeeting({
      meetingId: 'meeting-1',
      title: '',
      createdAt: '',
      messages: [{ ...message(speaker), originalText: 'Keep this.', translatedText: '' }],
    })
    assert.equal(meeting.messages[0].speaker, 'speaker1')
  }
})

test('voice cards show all recognized speakers while keeping typed messages separate', () => {
  const VoiceArea = loadSource('src/components/VoiceArea.tsx').default
  const html = renderToStaticMarkup(
    React.createElement(VoiceArea, {
      messages: [
        message('speaker1'),
        message('speaker3', 1),
        message('speaker11', 2),
        message('me', 3),
      ],
      isRecording: false,
      isProcessing: false,
    })
  )

  assert.match(html, /화자 1</)
  assert.match(html, /화자 3</)
  assert.match(html, /화자 11</)
  assert.match(html, /Utterance 1\./)
  assert.match(html, /Utterance 2\./)
  assert.doesNotMatch(html, /Utterance 3\./)
})

test('the pending transcript remains visible for a third speaker', () => {
  const VoiceArea = loadSource('src/components/VoiceArea.tsx').default
  const html = renderToStaticMarkup(
    React.createElement(VoiceArea, {
      messages: [],
      isRecording: true,
      isProcessing: false,
      pendingTranscript: {
        messageId: 'partial',
        speaker: 'speaker3',
        text: 'Third speaker is speaking',
      },
    })
  )

  assert.match(html, /화자 3</)
  assert.match(html, /Third speaker is speaking/)
})

test('the shared chat view renders additional speakers without falling back to Speaker 1', () => {
  const ChatArea = loadSource('src/components/ChatArea.tsx').default
  const html = renderToStaticMarkup(
    React.createElement(ChatArea, {
      messages: [message('speaker3')],
      isRecording: true,
      isProcessing: false,
      pendingTranscript: { messageId: 'partial', speaker: 'speaker11', text: 'Next speaker' },
    })
  )

  assert.match(html, /Speaker 3/)
  assert.match(html, />S11</)
  assert.doesNotMatch(html, /Speaker 1</)
})
