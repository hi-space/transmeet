import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import {
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  DeleteCommand,
  ScanCommand,
} from '@aws-sdk/lib-dynamodb';
import { randomUUID } from 'crypto';

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: process.env.REGION })
);

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
};

function respond(statusCode: number, body: unknown): APIGatewayProxyResult {
  return { statusCode, headers, body: JSON.stringify(body) };
}

export const handler = async (
  event: APIGatewayProxyEvent
): Promise<APIGatewayProxyResult> => {
  const method = event.httpMethod;
  const meetingId = event.pathParameters?.id;

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
          );
          if (!result.Item) {
            return respond(404, { error: 'Meeting not found' });
          }
          return respond(200, result.Item);
        }

        // GET /meetings - list all
        const result = await ddb.send(
          new ScanCommand({
            TableName: process.env.MEETINGS_TABLE,
            ProjectionExpression: 'meetingId, title, createdAt, #st',
            ExpressionAttributeNames: { '#st': 'status' },
          })
        );
        const items = (result.Items ?? []).sort(
          (a, b) =>
            new Date(b.createdAt as string).getTime() -
            new Date(a.createdAt as string).getTime()
        );
        return respond(200, items);
      }

      case 'POST': {
        // POST /meetings - create new meeting
        const body = JSON.parse(event.body ?? '{}') as { title?: string };
        const now = new Date().toISOString();
        const newMeeting = {
          meetingId: randomUUID(),
          title: body.title ?? `Meeting ${new Date().toLocaleString('ko-KR')}`,
          status: 'active',
          createdAt: now,
          updatedAt: now,
          messages: [],
          summary: null,
        };
        await ddb.send(
          new PutCommand({
            TableName: process.env.MEETINGS_TABLE,
            Item: newMeeting,
          })
        );
        return respond(201, newMeeting);
      }

      case 'DELETE': {
        // DELETE /meetings/{id}
        if (!meetingId) {
          return respond(400, { error: 'Missing meeting id' });
        }
        await ddb.send(
          new DeleteCommand({
            TableName: process.env.MEETINGS_TABLE,
            Key: { meetingId },
          })
        );
        return respond(204, '');
      }

      default:
        return respond(405, { error: 'Method not allowed' });
    }
  } catch (err) {
    console.error('Meetings handler error:', err);
    return respond(500, { error: 'Internal server error' });
  }
};
