import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

class Command {
  constructor(input) {
    this.input = input
  }
}
class QueryCommand extends Command {}
class BatchGetCommand extends Command {}

const TABLE = 'meeting-list-test'
const INDEX_FIELDS = ['meetingId', 'title', 'createdAt', 'status', 'messageCount']
const BRIEF = {
  brief: '결제 오류를 먼저 수정하고 금요일에 출시하기로 결정함',
  briefUpdatedAt: '2026-10-28T09:30:00Z',
  briefMessageCount: 20,
  briefLastMessageId: 'message-20',
}

function setup(records, { deferOnce = [], alwaysDefer = false } = {}) {
  process.env.MEETINGS_TABLE = TABLE
  const batches = []
  const deferred = new Set(deferOnce)
  const project = (record, fields) =>
    Object.fromEntries(
      fields.filter((field) => field in record).map((field) => [field, record[field]])
    )
  const ddb = {
    async send(command) {
      if (command instanceof QueryCommand) {
        const { IndexName, ExpressionAttributeValues, ProjectionExpression, ExclusiveStartKey } =
          command.input
        assert.equal(IndexName, 'userId-index')
        assert.equal(ExpressionAttributeValues[':uid'], 'owner')
        const fields = ProjectionExpression.replace('#st', 'status')
          .split(',')
          .map((s) => s.trim())
        assert.ok(
          fields.every((field) => INDEX_FIELDS.includes(field)),
          'The index has no brief fields'
        )
        const owned = records.filter((record) => record.userId === 'owner')
        const start = ExclusiveStartKey
          ? owned.findIndex((record) => record.meetingId === ExclusiveStartKey.meetingId) + 1
          : 0
        const page = owned.slice(start, start + 70)
        return {
          Items: page.map((record) => project(record, fields)),
          ...(start + page.length < owned.length
            ? { LastEvaluatedKey: { meetingId: page.at(-1).meetingId } }
            : {}),
        }
      }
      assert.ok(command instanceof BatchGetCommand, 'Listing must only read stored data')
      const request = command.input.RequestItems[TABLE]
      batches.push(request)
      assert.ok(request.Keys.length <= 100)
      const fields = request.ProjectionExpression.split(',').map((s) => s.trim())
      assert.ok(!fields.includes('messages'), 'A list response must not load transcripts')
      assert.ok(!fields.includes('summary'), 'A list response only needs the saved brief')
      const responses = []
      const unprocessed = []
      for (const key of request.Keys) {
        if (alwaysDefer || deferred.delete(key.meetingId)) {
          unprocessed.push(key)
          continue
        }
        const record = records.find((item) => item.meetingId === key.meetingId)
        if (record) responses.push(project(record, fields))
      }
      return {
        Responses: { [TABLE]: responses.reverse() },
        UnprocessedKeys: unprocessed.length ? { [TABLE]: { ...request, Keys: unprocessed } } : {},
      }
    },
  }
  const { handler } = loadTypeScript('infra/lambda/meetings/index.ts', {
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {} },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb },
      QueryCommand,
      BatchGetCommand,
    },
    '@aws-sdk/client-s3': { S3Client: class {} },
    '@aws-sdk/client-bedrock-runtime': {
      BedrockRuntimeClient: class {
        constructor() {
          throw new Error('Listing must never generate a summary')
        }
      },
    },
    crypto: {},
  })
  return {
    batches,
    list: () =>
      handler({
        httpMethod: 'GET',
        resource: '/meetings',
        requestContext: { authorizer: { claims: { sub: 'owner' } } },
      }),
  }
}

function meeting(id, extra = {}) {
  return {
    meetingId: id,
    userId: 'owner',
    title: '제품 출시 회의',
    createdAt: '2026-10-28T09:00:00Z',
    messageCount: 20,
    messages: [{ originalText: '전체 대화는 목록에 싣지 않습니다.' }],
    summary: '긴 회의 요약',
    ...extra,
  }
}

test('the meeting list includes saved briefs before opening a meeting and preserves audio-only meetings', async () => {
  const session = setup([
    meeting('brief', BRIEF),
    meeting('legacy'),
    meeting('audio', { messageCount: 0, hasAudio: true }),
    meeting('other-user', { userId: 'someone-else', ...BRIEF }),
  ])
  const response = await session.list()
  assert.equal(response.statusCode, 200)
  const items = JSON.parse(response.body)
  assert.equal(items.length, 3)
  const saved = items.find((item) => item.meetingId === 'brief')
  for (const [key, value] of Object.entries(BRIEF)) assert.equal(saved[key], value)
  assert.equal(items.find((item) => item.meetingId === 'legacy').brief, undefined)
  assert.equal(items.find((item) => item.meetingId === 'audio').hasAudio, true)
  assert.ok(items.every((item) => !('messages' in item) && !('summary' in item)))
})

test('paginated lists batch metadata and retry only unprocessed meetings', async () => {
  const records = Array.from({ length: 105 }, (_, i) =>
    meeting(`meeting-${i}`, { ...BRIEF, brief: `저장된 요약 ${i}` })
  )
  const session = setup(records, { deferOnce: ['meeting-2'] })
  const response = await session.list()
  assert.equal(response.statusCode, 200)
  const items = JSON.parse(response.body)
  assert.equal(items.length, 105)
  for (let i = 0; i < 105; i++) {
    assert.equal(items.find((item) => item.meetingId === `meeting-${i}`).brief, `저장된 요약 ${i}`)
  }
  assert.deepEqual(
    session.batches.map((batch) => batch.Keys.length),
    [100, 1, 5]
  )
})

test('unread audio metadata cannot be returned as an empty meeting eligible for deletion', async (t) => {
  t.mock.method(console, 'error', () => {})
  const session = setup([meeting('audio', { messageCount: 0, hasAudio: true })], {
    alwaysDefer: true,
  })
  const response = await session.list()
  assert.equal(response.statusCode, 500)
  assert.ok(session.batches.length > 1 && session.batches.length <= 5)
})
