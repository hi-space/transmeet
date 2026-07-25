import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda'
import { DynamoDBClient } from '@aws-sdk/client-dynamodb'
import {
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  DeleteCommand,
  ScanCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb'
import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime'
import { randomUUID } from 'crypto'

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.REGION }))
let _bedrock: BedrockRuntimeClient | null = null
function getBedrock() {
  if (!_bedrock) _bedrock = new BedrockRuntimeClient({ region: process.env.REGION })
  return _bedrock
}

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
}

function respond(statusCode: number, body: unknown): APIGatewayProxyResult {
  return { statusCode, headers, body: JSON.stringify(body) }
}

interface MeetingMessage {
  speaker: string
  originalText: string
  translatedText: string
}

// createdAt 이 없거나 파싱 불가한 레코드도 존재할 수 있다(중단된 세션/과거 데이터).
// NaN 을 반환하는 비교 함수는 정렬 결과 전체를 뒤섞으므로 항상 유효한 순서를 돌려준다.
function createdAtMs(item: Record<string, unknown>): number {
  const raw = item.createdAt
  if (typeof raw !== 'string') return -Infinity
  const ms = Date.parse(raw)
  return Number.isNaN(ms) ? -Infinity : ms
}

// 최신순. createdAt 없는 항목은 맨 뒤로 밀되 meetingId 로 순서를 고정한다.
function byCreatedAtDesc(
  a: Record<string, unknown>,
  b: Record<string, unknown>
): number {
  const am = createdAtMs(a)
  const bm = createdAtMs(b)
  if (am !== bm) return bm > am ? 1 : -1
  return String(a.meetingId ?? '').localeCompare(String(b.meetingId ?? ''))
}

export const handler = async (event: APIGatewayProxyEvent): Promise<APIGatewayProxyResult> => {
  const method = event.httpMethod
  const meetingId = event.pathParameters?.id
  const resource = event.resource ?? ''

  try {
    switch (method) {
      case 'GET': {
        if (meetingId) {
          // GET /meetings/{id}
          const result = await ddb.send(
            new GetCommand({
              TableName: process.env.MEETINGS_TABLE,
              Key: { meetingId },
            })
          )
          if (!result.Item) {
            return respond(404, { error: 'Meeting not found' })
          }
          return respond(200, result.Item)
        }

        // GET /meetings - list all (paginate through ScanCommand 1MB pages)
        const items: Record<string, unknown>[] = []
        let lastKey: Record<string, unknown> | undefined
        do {
          const page = await ddb.send(
            new ScanCommand({
              TableName: process.env.MEETINGS_TABLE,
              ProjectionExpression: 'meetingId, title, createdAt, #st, messageCount',
              ExpressionAttributeNames: { '#st': 'status' },
              ExclusiveStartKey: lastKey,
            })
          )
          if (page.Items) items.push(...page.Items)
          lastKey = page.LastEvaluatedKey
        } while (lastKey)
        items.sort(byCreatedAtDesc)
        return respond(200, items)
      }

      case 'POST': {
        if (resource.endsWith('/title')) {
          // POST /meetings/{id}/title — generate title with Bedrock
          if (!meetingId) {
            return respond(400, { error: 'Missing meeting id' })
          }

          const result = await ddb.send(
            new GetCommand({
              TableName: process.env.MEETINGS_TABLE,
              Key: { meetingId },
            })
          )

          if (!result.Item) {
            return respond(404, { error: 'Meeting not found' })
          }

          const messages = (result.Item.messages ?? []) as MeetingMessage[]
          if (messages.length === 0) {
            return respond(400, { error: 'No messages to generate title from' })
          }

          const transcript = messages
            .slice(0, 20)
            .map((m) => `[${m.speaker}] ${m.originalText}`)
            .join('\n')

          const bedrockRes = await getBedrock().send(
            new InvokeModelCommand({
              modelId: process.env.BEDROCK_MODEL_ID ?? '',
              contentType: 'application/json',
              accept: 'application/json',
              body: JSON.stringify({
                anthropic_version: 'bedrock-2023-05-31',
                max_tokens: 64,
                messages: [
                  {
                    role: 'user',
                    content: `다음 회의 내용을 보고 짧은 제목을 생성하세요 (10-20자, 한국어). 제목만 출력하세요.\n\n${transcript}`,
                  },
                ],
              }),
            })
          )

          const bedrockResult = JSON.parse(
            Buffer.from(bedrockRes.body as Uint8Array).toString('utf-8')
          ) as { content?: Array<{ text: string }> }

          const title = (bedrockResult.content?.[0]?.text ?? '').trim()

          await ddb.send(
            new UpdateCommand({
              TableName: process.env.MEETINGS_TABLE,
              Key: { meetingId },
              UpdateExpression: 'SET title = :t, updatedAt = :u',
              ExpressionAttributeValues: {
                ':t': title,
                ':u': new Date().toISOString(),
              },
            })
          )

          return respond(200, { title, meetingId })
        }

        // POST /meetings - create new meeting
        const body = JSON.parse(event.body ?? '{}') as { title?: string }
        const now = new Date().toISOString()
        const newMeeting = {
          meetingId: randomUUID(),
          title: body.title ?? `Meeting ${new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}`,
          status: 'active',
          createdAt: now,
          updatedAt: now,
          messages: [],
          // 목록 API 는 messages 를 투사하지 않는다. 빈 회의 자동 정리가
          // messageCount === 0 을 근거로 삼으므로 생성 시점부터 0 을 기록한다.
          messageCount: 0,
          summary: null,
        }
        await ddb.send(
          new PutCommand({
            TableName: process.env.MEETINGS_TABLE,
            Item: newMeeting,
          })
        )
        return respond(201, newMeeting)
      }

      case 'PUT': {
        if (resource.endsWith('/title')) {
          if (!meetingId) {
            return respond(400, { error: 'Missing meeting id' })
          }
          const body = JSON.parse(event.body ?? '{}') as { title?: string }
          const title = (body.title ?? '').trim()
          if (!title) {
            return respond(400, { error: 'Title cannot be empty' })
          }
          await ddb.send(
            new UpdateCommand({
              TableName: process.env.MEETINGS_TABLE,
              Key: { meetingId },
              UpdateExpression: 'SET title = :t, updatedAt = :u',
              ExpressionAttributeValues: {
                ':t': title,
                ':u': new Date().toISOString(),
              },
            })
          )
          return respond(200, { title, meetingId })
        }
        return respond(404, { error: 'Not found' })
      }

      case 'DELETE': {
        // DELETE /meetings/{id}
        if (!meetingId) {
          return respond(400, { error: 'Missing meeting id' })
        }
        await ddb.send(
          new DeleteCommand({
            TableName: process.env.MEETINGS_TABLE,
            Key: { meetingId },
          })
        )
        return respond(204, '')
      }

      default:
        return respond(405, { error: 'Method not allowed' })
    }
  } catch (err) {
    console.error('Meetings handler error:', err)
    return respond(500, { error: 'Internal server error' })
  }
}
