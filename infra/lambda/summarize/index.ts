import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import {
  DynamoDBDocumentClient,
  GetCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb';
import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from '@aws-sdk/client-bedrock-runtime';

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: process.env.REGION })
);
const bedrock = new BedrockRuntimeClient({ region: process.env.REGION });

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
};

interface MeetingMessage {
  id: string;
  speaker: string;
  originalText: string;
  translatedText: string;
  timestamp: string;
}

export const handler = async (
  event: APIGatewayProxyEvent
): Promise<APIGatewayProxyResult> => {
  const meetingId = event.pathParameters?.id;

  if (!meetingId) {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: 'Missing meeting id' }),
    };
  }

  try {
    // Fetch meeting from DynamoDB
    const result = await ddb.send(
      new GetCommand({
        TableName: process.env.MEETINGS_TABLE,
        Key: { meetingId },
      })
    );

    if (!result.Item) {
      return {
        statusCode: 404,
        headers,
        body: JSON.stringify({ error: 'Meeting not found' }),
      };
    }

    const messages = (result.Item.messages ?? []) as MeetingMessage[];
    if (messages.length === 0) {
      return {
        statusCode: 400,
        headers,
        body: JSON.stringify({ error: 'No messages to summarize' }),
      };
    }

    // Build transcript for Claude
    const transcript = messages
      .map(
        (m) =>
          `[${m.speaker}]\nEN: ${m.originalText}\nKO: ${m.translatedText}`
      )
      .join('\n\n');

    const prompt = `다음은 글로벌 회의의 대화 내용입니다. 아래 형식으로 한국어 요약을 작성해주세요.

## 회의 요약
- 주요 논의 사항 (bullet points)
- 결정 사항
- 후속 조치 (Action Items)

---
${transcript}`;

    const bedrockRes = await bedrock.send(
      new InvokeModelCommand({
        modelId: process.env.BEDROCK_MODEL_ID,
        contentType: 'application/json',
        accept: 'application/json',
        body: JSON.stringify({
          anthropic_version: 'bedrock-2023-05-31',
          max_tokens: 2048,
          messages: [{ role: 'user', content: prompt }],
        }),
      })
    );

    const bedrockResult = JSON.parse(
      Buffer.from(bedrockRes.body as Uint8Array).toString('utf-8')
    ) as { content?: Array<{ text: string }> };

    const summary = bedrockResult.content?.[0]?.text ?? '';

    // Persist summary
    await ddb.send(
      new UpdateCommand({
        TableName: process.env.MEETINGS_TABLE,
        Key: { meetingId },
        UpdateExpression: 'SET summary = :s, summarizedAt = :t',
        ExpressionAttributeValues: {
          ':s': summary,
          ':t': new Date().toISOString(),
        },
      })
    );

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ summary, meetingId }),
    };
  } catch (err) {
    console.error('Summarize error:', err);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: 'Failed to generate summary' }),
    };
  }
};
