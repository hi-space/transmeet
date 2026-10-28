import type { DynamoDBDocumentClient } from '@aws-sdk/lib-dynamodb'
import { BatchWriteCommand, QueryCommand } from '@aws-sdk/lib-dynamodb'
import type { BatchWriteCommandInput } from '@aws-sdk/lib-dynamodb'

// BatchWrite 한 테이블 몫의 요청 목록 (UnprocessedItems 도 같은 모양으로 돌아온다).
type WriteRequests = NonNullable<BatchWriteCommandInput['RequestItems']>[string]

// transmeet-messages 테이블에 저장된 회의 대화 한 건 (키 meetingId·sk 는 제외).
export interface StoredMessage {
  id: string
  speaker: string
  originalText: string
  translatedText: string
  detectedLanguage: string
  timestamp: string
  targetLanguage?: 'ko' | 'en'
}

const BATCH_SIZE = 25
// BatchWrite 의 UnprocessedItems 를 다시 보내는 간격(ms). 최대 3번 재시도한다.
const RETRY_DELAYS_MS = [50, 100, 200]

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

// 미팅의 대화를 정렬 키(timestamp#id) 오름차순으로 모두 읽는다. limit 이 있으면 앞에서 그만큼만.
// limit 이 0 이하면 읽지 않고 빈 배열 (DynamoDB 는 Limit<1 을 거부한다).
export async function loadMessages(
  ddb: DynamoDBDocumentClient,
  meetingId: string,
  opts: { limit?: number } = {}
): Promise<StoredMessage[]> {
  const { limit } = opts
  if (limit !== undefined && limit <= 0) return []
  const messages: StoredMessage[] = []
  let startKey: Record<string, unknown> | undefined
  do {
    const result = await ddb.send(
      new QueryCommand({
        TableName: process.env.MESSAGES_TABLE,
        KeyConditionExpression: 'meetingId = :m',
        ExpressionAttributeValues: { ':m': meetingId },
        ConsistentRead: true,
        ScanIndexForward: true,
        ExclusiveStartKey: startKey,
        ...(limit !== undefined ? { Limit: limit - messages.length } : {}),
      })
    )
    for (const item of result.Items ?? []) {
      // 키 속성은 응답에 싣지 않는다.
      const { meetingId: _m, sk: _sk, ...message } = item
      messages.push(message as StoredMessage)
    }
    startKey = result.LastEvaluatedKey
  } while (startKey && (limit === undefined || messages.length < limit))
  return limit !== undefined ? messages.slice(0, limit) : messages
}

// 미팅의 대화를 전부 지운다. 키만 읽어 25개씩 BatchWrite 하고, 처리 못 한 항목은 재시도한다.
// opts.sleep 은 테스트에서 실제로 기다리지 않도록 바꿔 끼우는 용도.
export async function deleteMessages(
  ddb: DynamoDBDocumentClient,
  meetingId: string,
  opts: { sleep?: (ms: number) => Promise<void> } = {}
): Promise<void> {
  const wait = opts.sleep ?? sleep
  const table = process.env.MESSAGES_TABLE as string
  const keys: Array<{ meetingId: string; sk: string }> = []
  let startKey: Record<string, unknown> | undefined
  do {
    const result = await ddb.send(
      new QueryCommand({
        TableName: table,
        KeyConditionExpression: 'meetingId = :m',
        ExpressionAttributeValues: { ':m': meetingId },
        ProjectionExpression: 'meetingId, sk',
        ConsistentRead: true,
        ExclusiveStartKey: startKey,
      })
    )
    for (const item of result.Items ?? []) {
      keys.push({ meetingId: item.meetingId, sk: item.sk })
    }
    startKey = result.LastEvaluatedKey
  } while (startKey)

  for (let i = 0; i < keys.length; i += BATCH_SIZE) {
    let pending: WriteRequests = keys
      .slice(i, i + BATCH_SIZE)
      .map((Key) => ({ DeleteRequest: { Key } }))
    for (let attempt = 0; ; attempt++) {
      const result = await ddb.send(
        new BatchWriteCommand({ RequestItems: { [table]: pending } })
      )
      pending = result.UnprocessedItems?.[table] ?? []
      if (pending.length === 0) break
      if (attempt >= RETRY_DELAYS_MS.length) {
        throw new Error('Could not delete meeting messages')
      }
      await wait(RETRY_DELAYS_MS[attempt])
    }
  }
}
