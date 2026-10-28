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
import {
  CUSTOM_SUMMARY_STYLE,
  DEFAULT_SUMMARY_STYLE,
  isSummaryStyle,
  type SummaryCustom,
} from '../../../src/lib/summaryStyles'
import { deleteMessages, loadMessages } from '../shared/messages'

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.REGION }))
const s3 = new S3Client({ region: process.env.REGION })
// 발행된 페이지 기록은 미팅 항목이 아니라 별도 테이블에 있다(미팅 항목은 400 KB 한도에 닿을 수 있다).
const PAGES_TABLE = process.env.PAGES_TABLE ?? 'transmeet-pages'
// 내 양식 사본(meetingId 키). 회의 항목은 발언 전체를 담아 400 KB 한도에 가까우므로 따로 둔다.
const SNAPSHOTS_TABLE = () => process.env.SUMMARY_SNAPSHOTS_TABLE ?? 'transmeet-summary-snapshots'
// UpdateItem 은 키가 없으면 항목을 새로 만든다. 그사이 지워진 미팅을 되살리지 않도록 갱신에 붙인다.
const MEETING_EXISTS = 'attribute_exists(meetingId)'

const isConditionFailed = (err: unknown) =>
  err instanceof Error && err.name === 'ConditionalCheckFailedException'

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
// 한 줄 요약과 녹음 여부는 userId-index 에 없다. 기존 인덱스를 재생성하지 않고
// 기본 테이블에서 목록에 필요한 필드만 함께 읽는다.
async function attachListMetadata(items: Record<string, unknown>[]): Promise<void> {
  const table = process.env.MEETINGS_TABLE!
  const byId = new Map(items.map((item) => [String(item.meetingId), item]))
  const ids = [...byId.keys()]
  for (let i = 0; i < ids.length; i += 100) {
    let keys = ids.slice(i, i + 100).map((meetingId) => ({ meetingId }))
    for (let attempt = 0; keys.length; attempt++) {
      const result = await ddb.send(
        new BatchGetCommand({
          RequestItems: {
            [table]: {
              Keys: keys,
              ProjectionExpression:
                'meetingId, hasAudio, brief, briefUpdatedAt, briefMessageCount, briefLastMessageId',
            },
          },
        })
      )
      for (const metadata of result.Responses?.[table] ?? []) {
        const item = byId.get(String(metadata.meetingId))
        if (item) Object.assign(item, metadata)
      }
      keys = (result.UnprocessedKeys?.[table]?.Keys ?? []).map((key) => ({
        meetingId: String(key.meetingId),
      }))
      if (!keys.length) break
      // 녹음 여부를 못 읽은 회의를 빈 회의로 반환하면 프런트가 삭제할 수 있다.
      if (attempt >= 3) throw new Error('Could not load meeting list metadata')
      await new Promise((resolve) => setTimeout(resolve, 50 * 2 ** attempt))
    }
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

async function loadSummaryCustom(meetingId: string, userId: string | undefined): Promise<SummaryCustom | undefined> {
  const res = await ddb.send(new GetCommand({ TableName: SNAPSHOTS_TABLE(), Key: { meetingId } }))
  if (!res.Item || res.Item.userId !== userId) return undefined
  const { templateId, name, kind, body, templateUpdatedAt } = res.Item
  return { templateId, name, kind, body, templateUpdatedAt }
}

function respond(statusCode: number, body: unknown): APIGatewayProxyResult {
  return { statusCode, headers, body: JSON.stringify(body) }
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
          // 대화는 메시지 테이블에 있다. 예전 항목에 남은 messages 속성은 버리고 테이블 값만 쓴다.
          const { messages: _legacyMessages, ...meeting } = result.Item
          const messages = await loadMessages(ddb, meetingId)
          const page = await loadPage(meetingId)
          const summaryCustom =
            result.Item.summaryStyle === CUSTOM_SUMMARY_STYLE ? await loadSummaryCustom(meetingId, userId) : undefined
          return respond(200, {
            ...meeting,
            messages,
            ...(page ? { page: toMeetingPage(page) } : {}),
            ...(summaryCustom ? { summaryCustom } : {}),
          })
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
        await attachListMetadata(items)
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

          // 제목에는 앞 20개 발언이면 충분하다.
          const messages = await loadMessages(ddb, meetingId, { limit: 20 })
          if (messages.length === 0) {
            return respond(400, { error: 'No messages to generate title from' })
          }

          const transcript = messages
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

          try {
            await ddb.send(
              new UpdateCommand({
                TableName: process.env.MEETINGS_TABLE,
                Key: { meetingId },
                UpdateExpression: 'SET title = :t, updatedAt = :u',
                ConditionExpression: MEETING_EXISTS,
                ExpressionAttributeValues: {
                  ':t': title,
                  ':u': new Date().toISOString(),
                },
              })
            )
          } catch (err) {
            // 제목을 만드는 사이 미팅이 지워졌다
            if (isConditionFailed(err)) return respond(404, { error: 'Meeting not found' })
            throw err
          }

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
          // 대화는 메시지 테이블에 쌓이므로 미팅 항목에는 messages 를 두지 않는다.
          // 빈 회의 자동 정리가 messageCount === 0 을 근거로 삼으므로 생성 시점부터 0 을 기록한다.
          messageCount: 0,
          summary: null,
          summaryStyle: DEFAULT_SUMMARY_STYLE,
        }
        await ddb.send(
          new PutCommand({
            TableName: process.env.MEETINGS_TABLE,
            Item: newMeeting,
          })
        )
        return respond(201, { ...newMeeting, messages: [] })
      }

      case 'PUT': {
        if (resource.endsWith('/summary-style')) {
          if (!meetingId) return respond(400, { error: 'Missing meeting id' })
          let body: { summaryStyle?: unknown; templateId?: unknown } | null
          try {
            body = JSON.parse(event.body ?? '{}')
          } catch {
            return respond(400, { error: 'Invalid request body' })
          }
          if (!isSummaryStyle(body?.summaryStyle)) {
            return respond(400, { error: 'Invalid summary style' })
          }
          const summaryStyle = body.summaryStyle
          let summaryCustom: SummaryCustom | undefined
          if (summaryStyle === CUSTOM_SUMMARY_STYLE) {
            // 내 양식은 서버가 양식 테이블에서 직접 읽어 사본을 둔다 — 클라이언트가 보낸 본문은 쓰지 않는다.
            const templateId = body.templateId
            if (typeof templateId !== 'string' || !templateId) {
              return respond(400, { error: 'Missing template id' })
            }
            const template = await ddb.send(new GetCommand({
              TableName: process.env.SUMMARY_TEMPLATES_TABLE,
              Key: { userId, templateId },
            }))
            if (!template.Item) return respond(404, { error: 'Template not found' })
            summaryCustom = {
              templateId,
              name: template.Item.name,
              kind: template.Item.kind,
              body: template.Item.body,
              templateUpdatedAt: template.Item.updatedAt,
            }
            // 사본을 먼저 둔다 — 선택값만 바뀌고 사본이 없는 순간이 없게(그 사이 요약은 이전 사본 또는 detailed)
            await ddb.send(new PutCommand({
              TableName: SNAPSHOTS_TABLE(),
              Item: { meetingId, userId, ...summaryCustom },
            }))
          }
          try {
            await ddb.send(new UpdateCommand({
              TableName: process.env.MEETINGS_TABLE,
              Key: { meetingId },
              UpdateExpression: 'SET summaryStyle = :style',
              ConditionExpression: `${MEETING_EXISTS} AND userId = :uid`,
              ExpressionAttributeValues: { ':style': summaryStyle, ':uid': userId },
            }))
          } catch (err) {
            // 그사이 지워졌거나 소유자가 아니면 없는 미팅으로 본다
            if (isConditionFailed(err)) return respond(404, { error: 'Meeting not found' })
            throw err
          }
          if (summaryCustom) return respond(200, { meetingId, summaryStyle, summaryCustom })
          return respond(200, { meetingId, summaryStyle })
        }
        if (resource.endsWith('/title')) {
          if (!meetingId) {
            return respond(400, { error: 'Missing meeting id' })
          }
          const body = JSON.parse(event.body ?? '{}') as { title?: string }
          const title = (body.title ?? '').trim()
          if (!title) {
            return respond(400, { error: 'Title cannot be empty' })
          }
          try {
            await ddb.send(
              new UpdateCommand({
                TableName: process.env.MEETINGS_TABLE,
                Key: { meetingId },
                UpdateExpression: 'SET title = :t, updatedAt = :u',
                ConditionExpression: MEETING_EXISTS,
                ExpressionAttributeValues: {
                  ':t': title,
                  ':u': new Date().toISOString(),
                },
              })
            )
          } catch (err) {
            if (isConditionFailed(err)) return respond(404, { error: 'Meeting not found' })
            throw err
          }
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
        await ddb.send(new DeleteCommand({ TableName: SNAPSHOTS_TABLE(), Key: { meetingId } }))
        // 대화를 먼저 지운다. 실패하면 던져서 500 으로 끝내고 미팅 항목은 남긴다(재시도 가능).
        await deleteMessages(ddb, meetingId)
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
