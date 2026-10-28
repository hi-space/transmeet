import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = ts.createSourceFile(
  'page.tsx',
  readFileSync(new URL('../src/app/page.tsx', import.meta.url), 'utf8'),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX
)

function findEndHandler(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'handleEndMeeting') {
    return node.initializer.arguments[0]
  }
  return ts.forEachChild(node, findEndHandler)
}

// Exercise the actual page callback without mounting the entire browser workspace.
const handler = findEndHandler(source)
assert.ok(handler, 'The meeting-end callback must exist')
const { outputText } = ts.transpileModule(`const handler = ${handler.getText(source)}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
})

function setup({ connected = true, confirmed = true, recording = true } = {}) {
  const sent = []
  const endStates = []
  let pendingAudio = recording ? 'final-buffered-wav' : null
  let capturing = recording
  let confirmations = 0
  let flushes = 0
  const scope = {
    activeMeetingId: 'meeting-1',
    isRecording: recording,
    window: {
      confirm: () => {
        confirmations++
        return confirmed
      },
    },
    meetingsRef: { current: [{ id: 'meeting-1', messages: [] }] },
    settings: { emailOnEnd: true },
    flushAudio: () => {
      flushes++
      if (pendingAudio && connected) sent.push({ action: 'sendAudio', audioData: pendingAudio })
      pendingAudio = null
    },
    sendEndMeeting: (meetingId, emailOnEnd, messages) => {
      if (!connected) return false
      sent.push({ action: 'endMeeting', meetingId, emailOnEnd, messages })
      return true
    },
    resetLocalRecording: () => {
      scope.flushAudio()
      capturing = false
    },
    setMeetingEnd: (state) => endStates.push(state),
    setTimeout: () => {},
    lastSummarizedCountRef: { current: {} },
    setMeetings: () => {},
    setRightPanel: () => {},
    setSummaryOpen: () => {},
  }
  const endMeeting = new Function(...Object.keys(scope), `${outputText}; return handler`)(
    ...Object.values(scope)
  )
  return {
    endMeeting,
    sent,
    endStates,
    isCapturing: () => capturing,
    confirmations: () => confirmations,
    flushes: () => flushes,
  }
}

test('ending a recording sends its final audio before the server closes audio intake', () => {
  const session = setup()
  session.endMeeting()

  assert.deepEqual(session.sent, [
    { action: 'sendAudio', audioData: 'final-buffered-wav' },
    { action: 'endMeeting', meetingId: 'meeting-1', emailOnEnd: true, messages: [] },
  ])
  assert.equal(session.isCapturing(), false)
  assert.deepEqual(session.endStates, [{ meetingId: 'meeting-1', phase: 'audio' }])
})

test('a failed meeting-end send keeps local capture running and reports the connection error', () => {
  const session = setup({ connected: false })
  session.endMeeting()

  assert.equal(session.isCapturing(), true)
  assert.deepEqual(session.sent, [])
  assert.equal(session.endStates.length, 1)
  assert.equal(session.endStates[0].phase, 'error')
  assert.ok(session.endStates[0].error)
})

test('cancelling meeting end leaves the recording and buffered audio untouched', () => {
  const session = setup({ confirmed: false })
  session.endMeeting()

  assert.equal(session.isCapturing(), true)
  assert.equal(session.flushes(), 0)
  assert.deepEqual(session.sent, [])
  assert.deepEqual(session.endStates, [])
})

test('an already stopped recording can end without another audio flush or confirmation', () => {
  const session = setup({ recording: false })
  session.endMeeting()

  assert.equal(session.confirmations(), 0)
  assert.equal(session.flushes(), 0)
  assert.deepEqual(session.sent, [
    { action: 'endMeeting', meetingId: 'meeting-1', emailOnEnd: true, messages: [] },
  ])
})
