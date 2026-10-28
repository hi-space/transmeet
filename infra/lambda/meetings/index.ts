import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda'
import { DynamoDBClient } from '@aws-sdk/client-dynamodb'
import {
  DynamoDBDocumentClient,
  BatchGetCommand,
  GetCommand,
  PutCommand,
  DeleteCommand,
  QueryCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb'
import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime'
import { S3Client, DeleteObjectCommand, DeleteObjectsCommand, ListObjectsV2Command } from '@aws-sdk/client-s3'
import { randomUUID } from 'crypto'
import { callerId, ownsMeeting } from '../shared/owner'

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.REGION }))
const s3 = new S3Client({ region: process.env.REGION })
// 발행된 페이지 기록은 미팅 항목이 아니라 별도 테이블에 있다(미팅 항목은 400 KB 한도에 닿을 수 있다).
const PAGES_TABLE = process.env.PAGES_TABLE ?? 'transmeet-pages'

interface PageRecord {
  meetingId: string
  pageId: string
  url: string
  title?: string
  shareSummary?: string
  generatedAt?: string
  updatedAt?: string
}

async function loadPage(meetingId: string): Promise<PageRecord | undefined> {
  const result = await ddb.send(new GetCommand({ TableName: PAGES_TABLE, Key: { meetingId } }))
  const item = result.Item as PageRecord | undefined
  return item?.pageId ? item : undefined
}

// 응답의 `page` 는 프런트의 MeetingPage 모양(pageId·url·title·shareSummary·generatedAt)이다.
function toMeetingPage(page: PageRecord) {
  const { meetingId: _meetingId, updatedAt: _updatedAt, ...rest } = page
  return rest
}
// 발언이 0개인 미팅에 녹음이 있는지 붙인다. 프런트는 발언 0개 미팅을 빈 미팅으로 보고 지우는데,
// STT 가 실패해도 녹음은 남아 있을 수 있다. hasAudio 는 userId-index 에 투영되지 않아(GSI 투영을 바꾸면
// 인덱스를 다시 만들어야 한다) 빈 미팅만 기본 테이블에서 읽는다 — 보통 몇 개뿐이다.
async function attachHasAudio(items: Record<string, unknown>[]): Promise<void> {
  const empty = items.filter((m) => !m.messageCount)
  for (let i = 0; i < empty.length; i += 100) {
    const chunk = empty.slice(i, i + 100)
    const result = await ddb.send(
      new BatchGetCommand({
        RequestItems: {
          [process.env.MEETINGS_TABLE!]: {
            Keys: chunk.map((m) => ({ meetingId: m.meetingId })),
            ProjectionExpression: 'meetingId, hasAudio',
          },
        },
      })
    )
    const withAudio = new Set(
      (result.Responses?.[process.env.MEETINGS_TABLE!] ?? [])
        .filter((r) => r.hasAudio)
        .map((r) => r.meetingId)
    )
    for (const m of chunk) if (withAudio.has(m.meetingId)) m.hasAudio = true
  }
}

// 녹음 구간(parts/*.pcm)과 합친 MP3 를 모두 지운다. 접두사가 meetings/{id}/ 라 다른 미팅과 섞이지 않는다.
async function deleteMeetingAudio(meetingId: string): Promise<void> {
  const Bucket = process.env.AUDIO_BUCKET
  if (!Bucket) return
  let ContinuationToken: string | undefined
  do {
    const listed = await s3.send(
      new ListObjectsV2Command({ Bucket, Prefix: `meetings/${meetingId}/`, ContinuationToken })
    )
    const keys = (listed.Contents ?? []).flatMap((o) => (o.Key ? [{ Key: o.Key }] : []))
    if (keys.length) await s3.send(new DeleteObjectsCommand({ Bucket, Delete: { Objects: keys, Quiet: true } }))
    ContinuationToken = listed.IsTruncated ? listed.NextContinuationToken : undefined
  } while (ContinuationToken)
}

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
  const userId = callerId(event)
  if (!userId) {
    return respond(401, { error: 'Unauthorized' })
  }

  try {
    // /meetings/{id}/* 는 모두 호출자 소유 미팅만 다룬다
    if (meetingId && !(await ownsMeeting(ddb, meetingId, userId))) {
      return respond(404, { error: 'Meeting not found' })
    }

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
          const page = await loadPage(meetingId)
          return respond(200, page ? { ...result.Item, page: toMeetingPage(page) } : result.Item)
        }

        // GET /meetings - 호출자의 미팅 전부 (userId-index, 1MB 페이지 단위로 이어 받는다)
        const items: Record<string, unknown>[] = []
        let lastKey: Record<string, unknown> | undefined
        do {
          const page = await ddb.send(
            new QueryCommand({
              TableName: process.env.MEETINGS_TABLE,
              IndexName: 'userId-index',
              KeyConditionExpression: 'userId = :uid',
              ProjectionExpression: 'meetingId, title, createdAt, #st, messageCount',
              ExpressionAttributeNames: { '#st': 'status' },
              ExpressionAttributeValues: { ':uid': userId },
              ExclusiveStartKey: lastKey,
            })
          )
          if (page.Items) items.push(...page.Items)
          lastKey = page.LastEvaluatedKey
        } while (lastKey)
        await attachHasAudio(items)
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
          userId,
          title: body.title ?? new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }),
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
        // 페이지 기록과 발행된 HTML 도 함께 지운다. S3 삭제가 실패하면 기록은 남겨 링크가 보이게 둔다.
        const page = await loadPage(meetingId)
        if (page && process.env.PAGES_BUCKET) {
          await s3.send(
            new DeleteObjectCommand({ Bucket: process.env.PAGES_BUCKET, Key: `p/${meetingId}/${page.pageId}.html` })
          )
        }
        if (page) {
          await ddb.send(new DeleteCommand({ TableName: PAGES_TABLE, Key: { meetingId } }))
        }
        await deleteMeetingAudio(meetingId)
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
