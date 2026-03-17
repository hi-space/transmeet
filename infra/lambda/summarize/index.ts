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
      .map((m) => `[${m.speaker}]\nOriginal: ${m.originalText}\nTranslation: ${m.translatedText}`)
      .join('\n\n');

    const systemPrompt = `당신은 전문 회의록 작성자입니다. 회의 대화록을 분석하여 명확하고 구조화된 한국어 요약을 마크다운 형식으로 작성합니다.`;

    const userPrompt = `다음 회의 대화록을 분석하여 아래 형식에 맞게 한국어로 요약해 주세요.

## 개요
(미팅 전체를 한 문장으로 요약)

## 핵심 메시지
- (가장 중요한 내용 1-2개)

## 주요 포인트
- (주요 논의 사항 나열)

## 상세 노트
(세부 내용)

각 섹션을 ## 헤더와 - 불릿으로 작성하세요. 간결하되 핵심 내용을 빠짐없이 포함하세요.

---
${transcript}`;

    const modelId =
      process.env.SUMMARIZE_MODEL_ID ?? process.env.BEDROCK_MODEL_ID ?? '';

    const bedrockRes = await bedrock.send(
      new InvokeModelCommand({
        modelId,
        contentType: 'application/json',
        accept: 'application/json',
        body: JSON.stringify({
          anthropic_version: 'bedrock-2023-05-31',
          max_tokens: 4096,
          system: systemPrompt,
          messages: [{ role: 'user', content: userPrompt }],
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
