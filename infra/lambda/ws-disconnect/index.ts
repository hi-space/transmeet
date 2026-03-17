import type { APIGatewayProxyWebsocketEventV2 } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, DeleteCommand } from '@aws-sdk/lib-dynamodb';

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: process.env.REGION })
);

export const handler = async (
  event: APIGatewayProxyWebsocketEventV2
): Promise<{ statusCode: number; body: string }> => {
  const { connectionId } = event.requestContext;

  await ddb.send(
    new DeleteCommand({
      TableName: process.env.CONNECTIONS_TABLE,
      Key: { connectionId },
    })
  );

  console.log(`Disconnected: ${connectionId}`);
  return { statusCode: 200, body: 'Disconnected' };
};
