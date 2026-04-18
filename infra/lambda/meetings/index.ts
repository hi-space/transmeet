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

        // GET /meetings - list all
        const result = await ddb.send(
          new ScanCommand({
            TableName: process.env.MEETINGS_TABLE,
            ProjectionExpression: 'meetingId, title, createdAt, #st, messageCount',
            ExpressionAttributeNames: { '#st': 'status' },
          })
        )
        const items = (result.Items ?? []).sort(
          (a, b) =>
            new Date(b.createdAt as string).getTime() - new Date(a.createdAt as string).getTime()
        )
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
          title: body.title ?? `Meeting ${new Date().toLocaleString('ko-KR')}`,
          status: 'active',
          createdAt: now,
          updatedAt: now,
          messages: [],
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
