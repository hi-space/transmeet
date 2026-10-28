import type { APIGatewayProxyEvent } from 'aws-lambda'
import type { DynamoDBDocumentClient } from '@aws-sdk/lib-dynamodb'
import { GetCommand } from '@aws-sdk/lib-dynamodb'

// API Gateway Cognito Authorizer 가 검증한 ID 토큰의 sub. 미팅 항목의 userId 와 같은 값이다.
export function callerId(event: APIGatewayProxyEvent): string | undefined {
  const sub = event.requestContext.authorizer?.claims?.sub
  return typeof sub === 'string' && sub ? sub : undefined
}

// 호출자가 소유한 미팅인지. 남의 미팅과 없는 미팅을 구분하지 않는다 — 둘 다 404 로 응답해 존재 여부를 흘리지 않는다.
export async function ownsMeeting(
  ddb: DynamoDBDocumentClient,
  meetingId: string,
  userId: string
): Promise<boolean> {
  const result = await ddb.send(
    new GetCommand({
      TableName: process.env.MEETINGS_TABLE,
      Key: { meetingId },
      ProjectionExpression: 'userId',
    })
  )
  return result.Item?.userId === userId
}
