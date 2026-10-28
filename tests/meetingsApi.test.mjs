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

function setup({ meetingItem, failBatch = false } = {}) {
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
        return { Items: stored.map((m) => ({ ...m })) }
      }
      if (command instanceof BatchWriteCommand) {
        if (failBatch) throw new Error('batch write failed')
        return {}
      }
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
