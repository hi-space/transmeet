import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import test from 'node:test'
import ts from 'typescript'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

const sources = Object.fromEntries(['meetings/index', 'summarize/index', 'shared/owner', 'shared/messages'].map((name) => [
  name,
  ts.transpileModule(
    readFileSync(new URL(`../infra/lambda/${name}.ts`, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
  ).outputText,
]))

class Command {
  constructor(input) {
    this.input = input
  }
}
class GetCommand extends Command {}
class PutCommand extends Command {}
class UpdateCommand extends Command {}
class QueryCommand extends Command {}
class BatchWriteCommand extends Command {}

process.env.MESSAGES_TABLE = 'transmeet-messages'

const generatedTitle = '제품 출시 일정 확정'

function setup() {
  let item
  let storedMessages = []
  const updates = []
  const ddb = {
    async send(command) {
      if (command instanceof GetCommand) return { Item: item }
      if (command instanceof QueryCommand) {
        assert.equal(command.input.TableName, 'transmeet-messages')
        return { Items: storedMessages }
      }
      if (command instanceof PutCommand) {
        item = command.input.Item
        return {}
      }
      assert.ok(command instanceof UpdateCommand)
      updates.push(command.input)
      return {}
    },
  }
  const dependencies = {
    '../../../src/lib/summaryStyles': loadTypeScript('src/lib/summaryStyles.ts'),
    '../shared/summaryPrompt': loadTypeScript('infra/lambda/shared/summaryPrompt.ts'),
    crypto: { randomUUID },
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {} },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb },
      GetCommand,
      PutCommand,
      UpdateCommand,
      QueryCommand,
      BatchWriteCommand,
    },
    '@aws-sdk/client-s3': { S3Client: class {} },
    '@aws-sdk/client-bedrock-runtime': {
      ConverseCommand: Command,
      BedrockRuntimeClient: class {
        async send(command) {
          const text = command.input.inferenceConfig.maxTokens === 64 ? generatedTitle : '회의 요약'
          return { output: { message: { content: [{ text }] } } }
        }
      },
    },
  }
  function load(name) {
    const exports = {}
    new Function('require', 'exports', 'Date', sources[name])((dependency) => {
      assert.ok(dependency in dependencies, `Unexpected import: ${dependency}`)
      return dependencies[dependency]
    }, exports, class extends Date {
      constructor(value = '2026-10-28T04:05:06Z') {
        super(value)
      }
    })
    return exports
  }
  dependencies['../shared/owner'] = load('shared/owner')
  dependencies['../shared/messages'] = load('shared/messages')
  const meetings = load('meetings/index')
  const summarize = load('summarize/index')
  const requestContext = { authorizer: { claims: { sub: 'user-1' } } }
  return {
    updates,
    create: (title) => meetings.handler({
      httpMethod: 'POST',
      resource: '/meetings',
      requestContext,
      body: JSON.stringify({ title }),
    }),
    summarize: () => {
      storedMessages = [{ meetingId: item.meetingId, sk: 't#message-1', id: 'message-1', originalText: '다음 주에 출시합니다.' }]
      return summarize.handler({
        httpMethod: 'POST',
        pathParameters: { id: item.meetingId },
        requestContext,
        body: '{}',
      })
    },
  }
}

for (const { name, title, expectedTitle } of [
  { name: 'new meetings use a timestamp and receive an AI title after summarizing', expectedTitle: generatedTitle },
  { name: 'timestamp defaults remain eligible for AI titles', title: '2026. 10. 28. 오전 9:05:06', expectedTitle: generatedTitle },
  { name: 'legacy Meeting defaults remain eligible for AI titles', title: 'Meeting 2026. 10. 28. 오후 1:05:06', expectedTitle: generatedTitle },
  { name: 'custom titles survive summarization', title: '제품 회의' },
  { name: 'custom titles beginning with a timestamp survive summarization', title: '2026. 10. 28. 오후 1:05:06 제품 회의' },
]) {
  test(name, async () => {
    const session = setup()
    const created = await session.create(title)
    assert.equal(created.statusCode, 201)
    assert.equal(JSON.parse(created.body).title, title ?? '2026. 10. 28. 오후 1:05:06')

    const summarized = await session.summarize()
    assert.equal(summarized.statusCode, 200)
    assert.equal(JSON.parse(summarized.body).title, expectedTitle)
    const titleUpdates = session.updates.filter((update) => update.UpdateExpression.startsWith('SET title'))
    assert.equal(titleUpdates.length, expectedTitle ? 1 : 0)
    if (expectedTitle) {
      assert.equal(titleUpdates[0].ExpressionAttributeValues[':t'], expectedTitle)
    }
  })
}
