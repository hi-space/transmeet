import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// Run the Lambda itself; replace only its external AWS clients.
const { outputText } = ts.transpileModule(
  readFileSync(new URL('../infra/lambda/summarize/index.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
)

class Command {
  constructor(input) {
    this.input = input
  }
}
class GetCommand extends Command {}
class UpdateCommand extends Command {}
class ConverseCommand extends Command {}

function setup({
  title = '제품 출시 회의',
  modelText = '출시 일정을 다음 주로 확정하고 결제 오류를 우선 해결하기로 함',
  owner = 'user-1',
  saveError,
  beforeRead,
  enforceOrdering = false,
  clock = Date,
} = {}) {
  const updates = []
  const modelRequests = []
  const item = {
    meetingId: 'meeting-1',
    userId: owner,
    title,
    summary: '기존 상세 요약',
    messages: [{ id: 'saved-1', originalText: '저장된 첫 발언', speaker: 'speaker1' }],
  }
  const ddb = {
    async send(command) {
      if (command instanceof GetCommand) {
        if (beforeRead) await beforeRead()
        return { Item: item }
      }
      assert.ok(command instanceof UpdateCommand)
      if (saveError) throw saveError
      if (enforceOrdering) {
        const values = command.input.ExpressionAttributeValues
        assert.match(command.input.ConditionExpression, /attribute_exists\(meetingId\)/)
        assert.match(command.input.ConditionExpression, /briefUpdatedAt\s*<=?\s*:t/)
        if (item.briefUpdatedAt && item.briefUpdatedAt >= values[':t']) {
          throw Object.assign(new Error('A newer brief is already saved'), {
            name: 'ConditionalCheckFailedException',
          })
        }
        Object.assign(item, {
          brief: values[':b'],
          briefUpdatedAt: values[':t'],
          briefMessageCount: values[':c'],
        })
      }
      updates.push(command.input)
      return {}
    },
  }
  const bedrock = {
    async send(command) {
      modelRequests.push(command.input)
      return { output: { message: { content: [{ text: modelText }] } } }
    },
  }
  const dependencies = {
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {} },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb },
      GetCommand,
      UpdateCommand,
    },
    '@aws-sdk/client-bedrock-runtime': {
      BedrockRuntimeClient: class {
        send(command) {
          return bedrock.send(command)
        }
      },
      ConverseCommand,
    },
    '../shared/owner': {
      callerId: (event) => event.requestContext.authorizer.claims.sub,
    },
  }
  const exports = {}
  new Function('require', 'exports', 'Date', outputText)(
    (name) => {
      assert.ok(name in dependencies, `Unexpected import: ${name}`)
      return dependencies[name]
    },
    exports,
    clock
  )
  const request = (body) =>
    exports.handler({
      httpMethod: 'POST',
      pathParameters: { id: 'meeting-1' },
      requestContext: { authorizer: { claims: { sub: 'user-1' } } },
      body: JSON.stringify(body),
    })
  return { request, updates, modelRequests, item }
}

const messages = Array.from({ length: 20 }, (_, i) => ({
  id: `message-${i + 1}`,
  original: i === 19 ? '결제 오류부터 해결하고 다음 주 출시합니다.' : `제품 출시 논의 ${i + 1}`,
}))

test('a brief summarizes the live conversation without changing the title or detailed summary', async () => {
  const session = setup()
  const response = await session.request({ mode: 'brief', messages })
  const body = JSON.parse(response.body)

  assert.equal(response.statusCode, 200)
  assert.equal(body.brief, '출시 일정을 다음 주로 확정하고 결제 오류를 우선 해결하기로 함')
  assert.equal(body.briefMessageCount, 20)
  assert.equal(body.briefLastMessageId, 'message-20')
  assert.equal(body.meetingId, 'meeting-1')
  assert.equal(session.modelRequests.length, 1)
  const prompt = session.modelRequests[0].messages[0].content[0].text
  assert.ok(prompt.includes('제품 출시 논의 1'))
  assert.ok(prompt.includes('결제 오류부터 해결하고 다음 주 출시합니다.'))
  assert.ok(!prompt.includes('저장된 첫 발언'))
  assert.equal(session.updates.length, 1)
  assert.match(session.updates[0].UpdateExpression, /SET brief =/)
  assert.doesNotMatch(session.updates[0].UpdateExpression, /\b(title|summary)\s*=/)
})

test('brief generation also leaves an auto-default title untouched', async () => {
  const session = setup({ title: 'Meeting 2026. 10. 28.' })
  const response = await session.request({ mode: 'brief', messages })
  assert.equal(response.statusCode, 200)
  assert.equal(session.modelRequests.length, 1)
  assert.equal(session.updates.length, 1)
  assert.doesNotMatch(session.updates[0].UpdateExpression, /\btitle\s*=/)
})

test('an empty or malformed live transcript cannot overwrite the saved brief', async () => {
  for (const input of [[], [{ id: 'blank', original: '   ' }], 'invalid', [{ original: 123 }]]) {
    const session = setup()
    const response = await session.request({ mode: 'brief', messages: input })
    assert.equal(response.statusCode, 400)
    assert.equal(session.modelRequests.length, 0)
    assert.equal(session.updates.length, 0)
  }
})

test('empty model output preserves the previous brief', async () => {
  const session = setup({ modelText: '  \n ' })
  const response = await session.request({ mode: 'brief', messages })
  assert.equal(response.statusCode, 502)
  assert.equal(session.updates.length, 0)
})

test('a brief is plain single-line text even when the model adds whitespace', async () => {
  const session = setup({ modelText: '  출시 일정 확정\n\n결제 오류 우선 해결  ' })
  const response = await session.request({ mode: 'brief', messages })
  assert.equal(JSON.parse(response.body).brief, '출시 일정 확정 결제 오류 우선 해결')
})

test('brief requests enforce meeting ownership before sending any conversation to the model', async () => {
  const session = setup({ owner: 'another-user' })
  const response = await session.request({ mode: 'brief', messages })
  assert.equal(response.statusCode, 404)
  assert.equal(session.modelRequests.length, 0)
  assert.equal(session.updates.length, 0)
})

test('an older brief cannot overwrite a newer result or recreate a deleted meeting', async () => {
  const session = setup({
    saveError: Object.assign(new Error('stale request'), {
      name: 'ConditionalCheckFailedException',
    }),
  })
  const response = await session.request({ mode: 'brief', messages })
  assert.equal(response.statusCode, 409)
  assert.equal(session.updates.length, 0)
})

test('request ordering is established before a delayed meeting ownership read', async () => {
  let now = Date.parse('2026-10-28T00:00:00Z')
  class TestDate extends Date {
    constructor() {
      super(now)
    }
  }
  let releaseRead
  let markReadStarted
  const readGate = new Promise((resolve) => {
    releaseRead = resolve
  })
  const readStarted = new Promise((resolve) => {
    markReadStarted = resolve
  })
  let reads = 0
  const session = setup({
    clock: TestDate,
    enforceOrdering: true,
    beforeRead: async () => {
      if (++reads === 1) {
        markReadStarted()
        await readGate
      }
    },
  })
  const older = session.request({ mode: 'brief', messages })
  await readStarted
  now += 1000
  const newer = await session.request({
    mode: 'brief',
    messages: [...messages, ...messages.map((m) => ({ ...m, id: `${m.id}-new` }))],
  })
  assert.equal(newer.statusCode, 200)
  now += 1000
  releaseRead()

  assert.equal((await older).statusCode, 409)
  assert.equal(session.item.briefMessageCount, 40)
  assert.equal(session.updates.length, 1)
})

test('normal summary requests retain their existing response and default-title behavior', async () => {
  const session = setup()
  const response = await session.request({})
  assert.equal(response.statusCode, 200)
  assert.equal(
    JSON.parse(response.body).summary,
    '출시 일정을 다음 주로 확정하고 결제 오류를 우선 해결하기로 함'
  )
  assert.equal(JSON.parse(response.body).brief, undefined)
  assert.equal(session.modelRequests.length, 1)
})

function loadApi() {
  const { outputText: apiCode } = ts.transpileModule(
    readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
  )
  const exports = {}
  new Function('require', 'exports', apiCode)((name) => {
    if (name === '@/lib/cognito') return { getIdToken: async () => null }
    if (name === '@/lib/speakers') return { normalizeSpeaker: (speaker) => speaker }
    throw new Error(`Unexpected import: ${name}`)
  }, exports)
  return exports
}

test('reloading a brief remaps its checkpoint after recorded utterances merge into bubbles', () => {
  const api = loadApi()
  const meeting = api.toMeeting({
    meetingId: 'meeting-1',
    title: '직접 지은 제목',
    createdAt: '2026-10-28T00:00:00Z',
    brief: '출시 준비와 결제 오류 해결',
    briefMessageCount: 3,
    briefLastMessageId: 'message-3',
    briefUpdatedAt: '2026-10-28T00:01:00Z',
    messages: Array.from({ length: 4 }, (_, i) => ({
      id: `message-${i + 1}`,
      speaker: i < 3 ? 'speaker1' : 'speaker2',
      originalText: `발언 ${i + 1}`,
      translatedText: '',
      timestamp: `2026-10-28T00:00:0${i}Z`,
    })),
  })

  assert.equal(meeting.messages.length, 2)
  assert.equal(meeting.brief, '출시 준비와 결제 오류 해결')
  assert.equal(meeting.briefMessageCount, 1)
  assert.equal(meeting.briefLastMessageId, 'message-3')
  assert.equal(meeting.title, '직접 지은 제목')
})

for (const sameMillisecond of [false, true]) {
  test(`a delayed utterance save${sameMillisecond ? ' within the same millisecond' : ''} cannot postpone the next brief`, () => {
    const api = loadApi()
    const saved = Array.from({ length: 40 }, (_, i) => ({
      id: `message-${i + 1}`,
      speaker: `speaker${(i % 2) + 1}`,
      originalText: `발언 ${i + 1}`,
      translatedText: '',
      timestamp: new Date(Date.UTC(2026, 9, 28, 0, 0, i * 10)).toISOString(),
    }))
    if (sameMillisecond) {
      // Python timestamps have microseconds; JavaScript dates only have milliseconds.
      saved[19].timestamp = '2026-10-28T00:03:10.123100+00:00'
      saved[20].timestamp = '2026-10-28T00:03:10.123200+00:00'
    }
    ;[saved[19], saved[20]] = [saved[20], saved[19]]
    const meeting = api.toMeeting({
      meetingId: 'meeting-1',
      title: '제품 출시 회의',
      createdAt: '2026-10-28T00:00:00Z',
      brief: '출시 일정 논의',
      briefMessageCount: 20,
      briefLastMessageId: 'message-20',
      messages: saved,
    })
    assert.equal(meeting.messages.length - meeting.briefMessageCount, 20)
    if (!sameMillisecond)
      assert.deepEqual(
        meeting.messages.slice(18, 21).map((message) => message.id),
        ['message-19', 'message-20', 'message-21']
      )
  })
}
