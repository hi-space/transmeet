import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

class QueryCommand { constructor(input) { this.input = input } }
class BatchWriteCommand { constructor(input) { this.input = input } }

function load() {
  return loadTypeScript('infra/lambda/shared/messages.ts', {
    '@aws-sdk/lib-dynamodb': { QueryCommand, BatchWriteCommand },
  })
}

const item = (n) => ({
  meetingId: 'm1',
  sk: `2026-01-01T00:00:${String(n).padStart(2, '0')}Z#id${n}`,
  id: `id${n}`,
  speaker: 'A',
  originalText: `o${n}`,
  translatedText: `t${n}`,
  detectedLanguage: 'en',
  timestamp: `2026-01-01T00:00:${String(n).padStart(2, '0')}Z`,
})

// 페이지 크기 제한이 있는 가짜 DynamoDB Query
function fakeDdb(items, pageSize, extra = () => ({})) {
  const calls = []
  return {
    calls,
    async send(command) {
      calls.push(command)
      if (command instanceof QueryCommand) {
        const start = command.input.ExclusiveStartKey?.i ?? 0
        const size = Math.min(pageSize, command.input.Limit ?? pageSize)
        const slice = items.slice(start, start + size)
        const next = start + size
        return {
          Items: slice,
          ...(next < items.length ? { LastEvaluatedKey: { i: next } } : {}),
        }
      }
      return extra(command, calls)
    },
  }
}

test('loadMessages follows LastEvaluatedKey', async () => {
  process.env.MESSAGES_TABLE = 'transmeet-messages'
  const { loadMessages } = load()
  const ddb = fakeDdb([1, 2, 3, 4, 5].map(item), 2)
  const result = await loadMessages(ddb, 'm1')
  assert.deepEqual(result.map((m) => m.id), ['id1', 'id2', 'id3', 'id4', 'id5'])
  assert.ok(result.every((m) => !('sk' in m) && !('meetingId' in m)))
  assert.equal(ddb.calls.length, 3)
  assert.equal(ddb.calls[0].input.TableName, 'transmeet-messages')
  assert.equal(ddb.calls[0].input.ConsistentRead, true)
})

test('loadMessages passes targetLanguage through when present', async () => {
  const { loadMessages } = load()
  const withLang = { ...item(1), targetLanguage: 'ko' }
  const result = await loadMessages(fakeDdb([withLang, item(2)], 10), 'm1')
  assert.equal(result[0].targetLanguage, 'ko')
  assert.ok(!('targetLanguage' in result[1]))
})

test('loadMessages honours limit', async () => {
  const { loadMessages } = load()
  const ddb = fakeDdb(Array.from({ length: 50 }, (_, i) => item(i)), 10)
  const result = await loadMessages(ddb, 'm1', { limit: 20 })
  assert.equal(result.length, 20)
  assert.equal(ddb.calls.length, 2)
})

test('deleteMessages batches by 25', async () => {
  const { deleteMessages } = load()
  const ddb = fakeDdb(Array.from({ length: 60 }, (_, i) => item(i)), 1000, () => ({}))
  await deleteMessages(ddb, 'm1')
  const batches = ddb.calls.filter((c) => c instanceof BatchWriteCommand)
  assert.deepEqual(batches.map((b) => b.input.RequestItems['transmeet-messages'].length), [25, 25, 10])
  assert.deepEqual(
    batches[0].input.RequestItems['transmeet-messages'][0],
    { DeleteRequest: { Key: { meetingId: 'm1', sk: item(0).sk } } }
  )
  const query = ddb.calls.find((c) => c instanceof QueryCommand)
  assert.equal(query.input.ProjectionExpression, 'meetingId, sk')
})

test('deleteMessages retries unprocessed items', async () => {
  const { deleteMessages } = load()
  const unprocessed = [
    { DeleteRequest: { Key: { meetingId: 'm1', sk: 'a' } } },
    { DeleteRequest: { Key: { meetingId: 'm1', sk: 'b' } } },
  ]
  let n = 0
  const ddb = fakeDdb([item(1), item(2)], 10, () =>
    n++ === 0 ? { UnprocessedItems: { 'transmeet-messages': unprocessed } } : {}
  )
  await deleteMessages(ddb, 'm1')
  const batches = ddb.calls.filter((c) => c instanceof BatchWriteCommand)
  assert.equal(batches.length, 2)
  assert.deepEqual(batches[1].input.RequestItems['transmeet-messages'], unprocessed)

  const always = fakeDdb([item(1)], 10, () => ({ UnprocessedItems: { 'transmeet-messages': unprocessed } }))
  await assert.rejects(deleteMessages(always, 'm1'), /Could not delete meeting messages/)
  assert.equal(always.calls.filter((c) => c instanceof BatchWriteCommand).length, 4)
})
