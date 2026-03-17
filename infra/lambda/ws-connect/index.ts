import type { APIGatewayProxyWebsocketEventV2 } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand } from '@aws-sdk/lib-dynamodb';

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: process.env.REGION })
);

export const handler = async (
  event: APIGatewayProxyWebsocketEventV2
): Promise<{ statusCode: number; body: string }> => {
  const { connectionId } = event.requestContext;
  const meetingId = event.queryStringParameters?.meetingId ?? null;

  await ddb.send(
    new PutCommand({
      TableName: process.env.CONNECTIONS_TABLE,
      Item: {
        connectionId,
        meetingId,
        connectedAt: new Date().toISOString(),
        // TTL: 24 hours
        ttl: Math.floor(Date.now() / 1000) + 86400,
      },
    })
  );

  console.log(`Connected: ${connectionId}, meetingId: ${meetingId}`);
  return { statusCode: 200, body: 'Connected' };
};
