import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda'
import { randomUUID } from 'crypto'
import { DynamoDBClient, ConditionalCheckFailedException } from '@aws-sdk/client-dynamodb'
import {
  DynamoDBDocumentClient,
  QueryCommand,
  PutCommand,
  UpdateCommand,
  DeleteCommand,
} from '@aws-sdk/lib-dynamodb'
import {
  SUMMARY_TEMPLATE_LIMITS,
  templateBodyMax,
  type SummaryTemplate,
  type SummaryTemplateKind,
} from '../../../src/lib/summaryStyles'

// 내 양식(사용자별 요약 프롬프트). 본인 것만 — 키가 (userId = Cognito sub, templateId) 라 남의 id 는 없는 것과 같다.
//   GET    /me/summary-templates              → { templates } (최근 수정순)
//   POST   /me/summary-templates              → 201 SummaryTemplate (사용자당 10개, 넘으면 409)
//   PUT    /me/summary-templates/{templateId} → 200 SummaryTemplate
//   DELETE /me/summary-templates/{templateId} → 204 (그 양식을 고른 회의의 사본은 그대로)

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.REGION }))
const TABLE = process.env.SUMMARY_TEMPLATES_TABLE

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
}

function respond(statusCode: number, body?: unknown): APIGatewayProxyResult {
  return { statusCode, headers, body: body === undefined ? '' : JSON.stringify(body) }
}

type TemplateInput = { name: string; kind: SummaryTemplateKind; body: string }

export function validateTemplateInput(value: unknown): TemplateInput | { error: string } {
  if (!value || typeof value !== 'object') return { error: 'Invalid request body' }
  const v = value as Record<string, unknown>
  const name = typeof v.name === 'string' ? v.name.trim() : ''
  if (!name || name.length > SUMMARY_TEMPLATE_LIMITS.nameMax) {
    return { error: `양식 이름은 1~${SUMMARY_TEMPLATE_LIMITS.nameMax}자입니다` }
  }
  if (v.kind !== 'instruction' && v.kind !== 'example') return { error: '양식 종류가 올바르지 않습니다' }
  const body = typeof v.body === 'string' ? v.body : ''
  const max = templateBodyMax(v.kind)
  if (!body.trim() || body.length > max) return { error: `양식 내용은 1~${max}자입니다` }
  return { name, kind: v.kind, body }
}

function toTemplate(item: Record<string, unknown>): SummaryTemplate {
  const { templateId, name, kind, body, createdAt, updatedAt } = item as unknown as SummaryTemplate
  return { templateId, name, kind, body, createdAt, updatedAt }
}

function parseBody(event: APIGatewayProxyEvent): unknown | undefined {
  try {
    return JSON.parse(event.body ?? '{}')
  } catch {
    return undefined
  }
}

export async function handler(event: APIGatewayProxyEvent): Promise<APIGatewayProxyResult> {
  const sub = event.requestContext.authorizer?.claims?.sub
  const userId = typeof sub === 'string' && sub ? sub : undefined
  if (!userId) return respond(401, { error: 'Unauthorized' })
  const templateId = event.pathParameters?.templateId

  try {
    if (event.httpMethod === 'GET') {
      const res = await ddb.send(new QueryCommand({
        TableName: TABLE,
        KeyConditionExpression: 'userId = :uid',
        ExpressionAttributeValues: { ':uid': userId },
      }))
      const templates = (res.Items ?? []).map(toTemplate)
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      return respond(200, { templates })
    }

    if (event.httpMethod === 'DELETE') {
      if (!templateId) return respond(400, { error: 'Missing template id' })
      await ddb.send(new DeleteCommand({
        TableName: TABLE,
        Key: { userId, templateId },
        ConditionExpression: 'attribute_exists(templateId)',
      }))
      return respond(204)
    }

    if (event.httpMethod !== 'POST' && event.httpMethod !== 'PUT') {
      return respond(405, { error: 'Method not allowed' })
    }
    const parsed = parseBody(event)
    if (parsed === undefined) return respond(400, { error: 'Invalid request body' })
    const input = validateTemplateInput(parsed)
    if ('error' in input) return respond(400, input)
    const now = new Date().toISOString()

    if (event.httpMethod === 'POST') {
      const count = await ddb.send(new QueryCommand({
        TableName: TABLE,
        KeyConditionExpression: 'userId = :uid',
        ExpressionAttributeValues: { ':uid': userId },
        Select: 'COUNT',
      }))
      if ((count.Count ?? 0) >= SUMMARY_TEMPLATE_LIMITS.perUser) {
        return respond(409, { error: `양식은 ${SUMMARY_TEMPLATE_LIMITS.perUser}개까지 저장할 수 있습니다` })
      }
      const item = { userId, templateId: randomUUID(), ...input, createdAt: now, updatedAt: now }
      await ddb.send(new PutCommand({ TableName: TABLE, Item: item }))
      return respond(201, toTemplate(item))
    }

    if (!templateId) return respond(400, { error: 'Missing template id' })
    const res = await ddb.send(new UpdateCommand({
      TableName: TABLE,
      Key: { userId, templateId },
      UpdateExpression: 'SET #n = :name, kind = :kind, body = :body, updatedAt = :now',
      ConditionExpression: 'attribute_exists(templateId)',
      ExpressionAttributeNames: { '#n': 'name' },
      ExpressionAttributeValues: { ':name': input.name, ':kind': input.kind, ':body': input.body, ':now': now },
      ReturnValues: 'ALL_NEW',
    }))
    return respond(200, toTemplate(res.Attributes ?? {}))
  } catch (err) {
    if (err instanceof ConditionalCheckFailedException) return respond(404, { error: 'Template not found' })
    console.error('Summary templates handler error:', err)
    return respond(500, { error: 'Internal server error' })
  }
}
