import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

const { outputText } = ts.transpileModule(
  readFileSync(new URL('../infra/lambda/meetings/index.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
)

class Command {
  constructor(input) {
    this.input = input
  }
}
class GetCommand extends Command {}
class PutCommand extends Command {}
class DeleteCommand extends Command {}
class QueryCommand extends Command {}
class UpdateCommand extends Command {}
class BatchGetCommand extends Command {}
class BatchWriteCommand extends Command {}

process.env.MEETINGS_TABLE = 'transmeet-meetings'
process.env.MESSAGES_TABLE = 'transmeet-messages'

const stored = [
  { meetingId: 'm1', sk: '2026-10-28T00:00:00Z#a', id: 'a', speaker: 's1', originalText: '안녕', translatedText: 'hi', detectedLanguage: 'ko', timestamp: '2026-10-28T00:00:00Z' },
  { meetingId: 'm1', sk: '2026-10-28T00:00:01Z#b', id: 'b', speaker: 's2', originalText: '네', translatedText: 'yes', detectedLanguage: 'ko', timestamp: '2026-10-28T00:00:01Z' },
]

function setup({ meetingItem, failBatch = false, updateError, queryGate } = {}) {
  const sent = []
  const ddb = {
    async send(command) {
      sent.push(command)
      if (command instanceof GetCommand) {
        const table = command.input.TableName
        if (table === 'transmeet-meetings') return { Item: meetingItem }
        return {}
      }
      if (command instanceof QueryCommand) {
        assert.equal(command.input.TableName, 'transmeet-messages')
        if (queryGate) await queryGate
        return { Items: stored.map((m) => ({ ...m })) }
      }
      if (command instanceof BatchWriteCommand) {
        if (failBatch) throw new Error('batch write failed')
        return {}
      }
      if (command instanceof UpdateCommand && updateError) throw updateError
      return {}
    },
  }
  const dependencies = {
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {} },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb },
      GetCommand, PutCommand, DeleteCommand, QueryCommand, UpdateCommand, BatchGetCommand, BatchWriteCommand,
    },
    '@aws-sdk/client-bedrock-runtime': { BedrockRuntimeClient: class {}, InvokeModelCommand: Command },
    '@aws-sdk/client-s3': {
      S3Client: class { async send() { return {} } },
      DeleteObjectCommand: Command, DeleteObjectsCommand: Command, ListObjectsV2Command: Command,
    },
    crypto: { randomUUID: () => 'new-meeting' },
    '../../../src/lib/summaryStyles': loadTypeScript('src/lib/summaryStyles.ts'),
  }
  dependencies['../shared/owner'] = loadTypeScript('infra/lambda/shared/owner.ts', {
    '@aws-sdk/lib-dynamodb': { GetCommand },
  })
  dependencies['../shared/messages'] = loadTypeScript('infra/lambda/shared/messages.ts', {
    '@aws-sdk/lib-dynamodb': { QueryCommand, BatchWriteCommand },
  })
  const exports = {}
  new Function('require', 'exports', outputText)((name) => {
    assert.ok(name in dependencies, `Unexpected import: ${name}`)
    return dependencies[name]
  }, exports)
  const requestContext = { authorizer: { claims: { sub: 'user-1' } } }
  const call = (httpMethod, resource, extra = {}) =>
    exports.handler({
      httpMethod,
      resource,
      requestContext,
      pathParameters: resource.includes('{id}') ? { id: 'm1' } : null,
      ...extra,
    })
  return { sent, call }
}

const ownedMeeting = () => ({ meetingId: 'm1', userId: 'user-1', title: 't', messages: [{ id: 'legacy' }] })

test('GET /meetings/{id} attaches messages from the messages table', async () => {
  const { call } = setup({ meetingItem: ownedMeeting() })
  const res = await call('GET', '/meetings/{id}')
  assert.equal(res.statusCode, 200)
  const body = JSON.parse(res.body)
  assert.deepEqual(body.messages.map((m) => m.id), ['a', 'b'])
  assert.ok(!('sk' in body.messages[0]))
  assert.ok(!body.messages.some((m) => m.id === 'legacy'))
})

test('POST /meetings stores no messages attribute', async () => {
  const { call, sent } = setup()
  const res = await call('POST', '/meetings', { body: JSON.stringify({ title: '새 회의' }) })
  assert.equal(res.statusCode, 201)
  const put = sent.find((c) => c instanceof PutCommand)
  assert.ok(!('messages' in put.input.Item))
  const body = JSON.parse(res.body)
  assert.deepEqual(body.messages, [])
  assert.equal(body.messageCount, 0)
})

test('DELETE deletes messages before the meeting item', async () => {
  const { call, sent } = setup({ meetingItem: ownedMeeting() })
  const res = await call('DELETE', '/meetings/{id}')
  assert.equal(res.statusCode, 204)
  const lastBatch = sent.map((c, i) => (c instanceof BatchWriteCommand ? i : -1)).filter((i) => i >= 0).pop()
  const meetingDelete = sent.findIndex(
    (c) => c instanceof DeleteCommand && c.input.TableName === 'transmeet-meetings'
  )
  assert.ok(lastBatch >= 0 && meetingDelete >= 0)
  assert.ok(lastBatch < meetingDelete)
  // 지울 키는 메시지 테이블에서 읽고, 같은 테이블에 BatchWrite 한다
  const keyQuery = sent.find((c) => c instanceof QueryCommand && c.input.ProjectionExpression === 'meetingId, sk')
  assert.equal(keyQuery?.input.TableName, 'transmeet-messages')
  assert.deepEqual(Object.keys(sent[lastBatch].input.RequestItems), ['transmeet-messages'])
})

test('DELETE keeps the meeting when message deletion fails', async () => {
  const { call, sent } = setup({ meetingItem: ownedMeeting(), failBatch: true })
  const res = await call('DELETE', '/meetings/{id}')
  assert.equal(res.statusCode, 500)
  assert.ok(!sent.some((c) => c instanceof DeleteCommand && c.input.TableName === 'transmeet-meetings'))
  const keyQuery = sent.find((c) => c instanceof QueryCommand && c.input.ProjectionExpression === 'meetingId, sk')
  assert.equal(keyQuery?.input.TableName, 'transmeet-messages')
})

const conditionFailed = () => Object.assign(new Error('The conditional request failed'), {
  name: 'ConditionalCheckFailedException',
})

test('PUT /meetings/{id}/title only updates an existing meeting', async () => {
  const { call, sent } = setup({ meetingItem: ownedMeeting() })
  const res = await call('PUT', '/meetings/{id}/title', { body: JSON.stringify({ title: '새 제목' }) })
  assert.equal(res.statusCode, 200)
  const update = sent.find((c) => c instanceof UpdateCommand)
  assert.equal(update.input.UpdateExpression, 'SET title = :t, updatedAt = :u')
  assert.equal(update.input.ConditionExpression, 'attribute_exists(meetingId)')
})

test('PUT /meetings/{id}/title returns 404 when the meeting was deleted meanwhile', async () => {
  const { call } = setup({ meetingItem: ownedMeeting(), updateError: conditionFailed() })
  const res = await call('PUT', '/meetings/{id}/title', { body: JSON.stringify({ title: '새 제목' }) })
  assert.equal(res.statusCode, 404)
  assert.deepEqual(JSON.parse(res.body), { error: 'Meeting not found' })
})

test('PUT /meetings/{id}/summary-style maps a failed condition to 404', async () => {
  const { call, sent } = setup({ meetingItem: ownedMeeting(), updateError: conditionFailed() })
  const res = await call('PUT', '/meetings/{id}/summary-style', { body: JSON.stringify({ summaryStyle: 'report' }) })
  assert.equal(res.statusCode, 404)
  const update = sent.find((c) => c instanceof UpdateCommand)
  assert.equal(update.input.ConditionExpression, 'attribute_exists(meetingId) AND userId = :uid')
})

test('GET /meetings/{id} reads the meeting item once (ownership check folded in)', async () => {
  const { call, sent } = setup({ meetingItem: ownedMeeting() })
  const res = await call('GET', '/meetings/{id}')
  assert.equal(res.statusCode, 200)
  const meetingGets = sent.filter(
    (c) => c instanceof GetCommand && c.input.TableName === 'transmeet-meetings'
  )
  assert.equal(meetingGets.length, 1)
})

test("GET /meetings/{id} hides another user's meeting as 404", async () => {
  const { call, sent } = setup({ meetingItem: { ...ownedMeeting(), userId: 'someone-else' } })
  const res = await call('GET', '/meetings/{id}')
  assert.equal(res.statusCode, 404)
  assert.ok(!sent.some((c) => c instanceof QueryCommand), 'must not read messages of a foreign meeting')
})

test('GET /meetings/{id} loads messages and page in parallel', async () => {
  let release
  const queryGate = new Promise((resolve) => { release = resolve })
  const { call, sent } = setup({ meetingItem: ownedMeeting(), queryGate })
  const pending = call('GET', '/meetings/{id}')
  // 메시지 Query 가 끝나지 않은 동안에도 페이지 Get 이 이미 나가 있어야 한다.
  await new Promise((resolve) => setTimeout(resolve, 10))
  const pageSent = sent.some((c) => c instanceof GetCommand && c.input.TableName === 'transmeet-pages')
  release()
  const res = await pending
  assert.equal(res.statusCode, 200)
  assert.ok(pageSent, 'page read should not wait for messages')
})

test("a GET sub-route still runs the ownership check for another user's meeting", async () => {
  const { call, sent } = setup({ meetingItem: { ...ownedMeeting(), userId: 'someone-else' } })
  const res = await call('GET', '/meetings/{id}/future-sub-route')
  assert.equal(res.statusCode, 404)
  assert.ok(!sent.some((c) => c instanceof QueryCommand))
})

test('GET /meetings/{id}?messages=0 returns the meeting without reading messages', async () => {
  const { call, sent } = setup({ meetingItem: { ...ownedMeeting(), summary: '요약' } })
  const res = await call('GET', '/meetings/{id}', { queryStringParameters: { messages: '0' } })
  assert.equal(res.statusCode, 200)
  const body = JSON.parse(res.body)
  assert.equal(body.summary, '요약')
  assert.ok(!('messages' in body), 'messages must be omitted so the client knows they are not loaded')
  assert.ok(!sent.some((c) => c instanceof QueryCommand))
})

test('GET /meetings/{id}/messages returns only the conversation', async () => {
  const { call } = setup({ meetingItem: ownedMeeting() })
  const res = await call('GET', '/meetings/{id}/messages')
  assert.equal(res.statusCode, 200)
  const body = JSON.parse(res.body)
  assert.deepEqual(body.messages.map((m) => m.id), ['a', 'b'])
  assert.ok(!('title' in body))
})

test("GET /meetings/{id}/messages hides another user's conversation as 404", async () => {
  const { call, sent } = setup({ meetingItem: { ...ownedMeeting(), userId: 'someone-else' } })
  const res = await call('GET', '/meetings/{id}/messages')
  assert.equal(res.statusCode, 404)
  assert.ok(!sent.some((c) => c instanceof QueryCommand))
})
