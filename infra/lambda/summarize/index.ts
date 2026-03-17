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

    const systemPrompt = `You are a professional meeting minutes writer. Analyze meeting transcripts and produce clear, structured Korean summaries in markdown format.`;

    const userPrompt = `Analyze the following meeting transcript and create a comprehensive meeting summary in Korean.

Include ALL of these sections:
1. **회의 개요**: 주요 주제, 참석자 역할 추정
2. **주요 논의 사항**: 각 토픽별로 논의된 내용 정리
3. **결정 사항**: 합의된 내용, 결론
4. **Action Items**: 후속 조치가 필요한 사항
5. **기타 메모**: 중요한 언급사항

Format as clean markdown using ## for sections and - for bullets. Be thorough but concise.

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
