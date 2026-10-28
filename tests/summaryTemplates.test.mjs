import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

class Command {
  constructor(input) {
    this.input = input
  }
}
class QueryCommand extends Command {}
class PutCommand extends Command {}
class UpdateCommand extends Command {}
class DeleteCommand extends Command {}
class ConditionalCheckFailedException extends Error {
  constructor() {
    super('conditional')
    this.name = 'ConditionalCheckFailedException'
  }
}

// (userId, templateId) 키의 메모리 테이블. Lambda 가 실제로 쓰는 조건식만 흉내 낸다.
function setup() {
  const rows = new Map()
  const key = (k) => `${k.userId}|${k.templateId}`
  const ddb = {
    async send(command) {
      const input = command.input
      if (command instanceof QueryCommand) {
        const uid = input.ExpressionAttributeValues[':uid']
        const items = [...rows.values()].filter((r) => r.userId === uid)
        return input.Select === 'COUNT' ? { Count: items.length } : { Items: items.map((r) => ({ ...r })) }
      }
      if (command instanceof PutCommand) {
        rows.set(key(input.Item), { ...input.Item })
        return {}
      }
      if (command instanceof UpdateCommand || command instanceof DeleteCommand) {
        const existing = rows.get(key(input.Key))
        if (!existing) throw new ConditionalCheckFailedException()
        if (command instanceof DeleteCommand) {
          rows.delete(key(input.Key))
          return {}
        }
        const values = input.ExpressionAttributeValues
        const updated = { ...existing, name: values[':name'], kind: values[':kind'], body: values[':body'], updatedAt: values[':now'] }
        rows.set(key(input.Key), updated)
        return { Attributes: { ...updated } }
      }
      throw new Error(`unexpected command ${command.constructor.name}`)
    },
  }
  const mod = loadTypeScript('infra/lambda/summary-templates/index.ts', {
    crypto: { randomUUID: (() => { let n = 0; return () => `tpl-${++n}` })() },
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {}, ConditionalCheckFailedException },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb }, QueryCommand, PutCommand, UpdateCommand, DeleteCommand,
    },
  })
  const call = (user, method, { id, body, raw } = {}) => mod.handler({
    httpMethod: method,
    pathParameters: id ? { templateId: id } : null,
    requestContext: { authorizer: { claims: user ? { sub: user } : {} } },
    body: raw ?? (body === undefined ? null : JSON.stringify(body)),
  }).then((r) => ({ ...r, json: r.body ? JSON.parse(r.body) : undefined }))
  return { rows, call, mod }
}

const INPUT = { name: '주간 회의', kind: 'instruction', body: '섹션은 이슈 / 원인 / 조치' }

test('create → list → update → delete round trip for the caller only', async () => {
  const { call } = setup()
  const created = await call('user-1', 'POST', { body: INPUT })
  assert.equal(created.statusCode, 201)
  assert.equal(created.json.name, '주간 회의')
  assert.ok(created.json.templateId && created.json.createdAt && created.json.updatedAt)
  assert.equal('userId' in created.json, false)

  const listed = await call('user-1', 'GET')
  assert.deepEqual(listed.json.templates.map((t) => t.templateId), [created.json.templateId])

  const updated = await call('user-1', 'PUT', { id: created.json.templateId, body: { ...INPUT, body: '바뀐 양식' } })
  assert.equal(updated.statusCode, 200)
  assert.equal(updated.json.body, '바뀐 양식')

  assert.equal((await call('user-1', 'DELETE', { id: created.json.templateId })).statusCode, 204)
  assert.deepEqual((await call('user-1', 'GET')).json.templates, [])
})

test("another user's templateId is 404 for PUT and DELETE and absent from GET", async () => {
  const { call, rows } = setup()
  const { json } = await call('user-1', 'POST', { body: INPUT })
  assert.equal((await call('user-2', 'PUT', { id: json.templateId, body: { ...INPUT, body: '탈취' } })).statusCode, 404)
  assert.equal((await call('user-2', 'DELETE', { id: json.templateId })).statusCode, 404)
  assert.deepEqual((await call('user-2', 'GET')).json.templates, [])
  assert.equal([...rows.values()][0].body, INPUT.body)
})

test('limits: name, body length per kind, kind, and count', async () => {
  const { call } = setup()
  const status = async (body) => (await call('user-1', 'POST', { body })).statusCode
  assert.equal(await status({ ...INPUT, name: '   ' }), 400)
  assert.equal(await status({ ...INPUT, name: '가'.repeat(31) }), 400)
  assert.equal(await status({ ...INPUT, body: '   ' }), 400)
  assert.equal(await status({ ...INPUT, kind: 'other' }), 400)
  assert.equal(await status({ ...INPUT, body: 'a'.repeat(6001) }), 400)
  assert.equal(await status({ ...INPUT, kind: 'example', body: 'a'.repeat(12001) }), 400)
  assert.equal(await status({ ...INPUT, body: 'a'.repeat(6000) }), 201)
  assert.equal(await status({ ...INPUT, kind: 'example', body: 'a'.repeat(12000) }), 201)
  for (let i = 0; i < 8; i++) assert.equal(await status(INPUT), 201)
  assert.equal(await status(INPUT), 409)
})

test('name is trimmed and the list is newest first', async () => {
  const { call } = setup()
  const first = await call('user-1', 'POST', { body: { ...INPUT, name: '  첫 양식  ' } })
  assert.equal(first.json.name, '첫 양식')
  await new Promise((r) => setTimeout(r, 5))
  const second = await call('user-1', 'POST', { body: { ...INPUT, name: '둘째' } })
  const names = (await call('user-1', 'GET')).json.templates.map((t) => t.name)
  assert.deepEqual(names, ['둘째', '첫 양식'])
  assert.ok(second.json.updatedAt >= first.json.updatedAt)
})

test('malformed JSON body → 400, unauthenticated (no sub) → 401, unknown id → 404', async () => {
  const { call } = setup()
  assert.equal((await call('user-1', 'POST', { raw: '{nope' })).statusCode, 400)
  assert.equal((await call(null, 'GET')).statusCode, 401)
  assert.equal((await call('user-1', 'PUT', { id: 'missing', body: INPUT })).statusCode, 404)
  assert.equal((await call('user-1', 'DELETE', { id: 'missing' })).statusCode, 404)
})
