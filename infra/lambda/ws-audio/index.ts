/**
 * WebSocket audio handler
 * Flow: audio chunk -> Whisper STT -> Bedrock translation -> WS response
 *
 * Expected message format:
 * {
 *   action: "sendAudio",
 *   audioData: "<base64 WAV>",
 *   meetingId: "<uuid>",
 *   speaker: "remote" | "local"
 * }
 */
import type { APIGatewayProxyWebsocketEventV2 } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, UpdateCommand } from '@aws-sdk/lib-dynamodb';
import {
  SageMakerRuntimeClient,
  InvokeEndpointCommand,
} from '@aws-sdk/client-sagemaker-runtime';
import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from '@aws-sdk/client-bedrock-runtime';
import {
  ApiGatewayManagementApiClient,
  PostToConnectionCommand,
  GoneException,
} from '@aws-sdk/client-apigatewaymanagementapi';

const ddb = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: process.env.REGION })
);
const sagemaker = new SageMakerRuntimeClient({ region: process.env.REGION });
const bedrock = new BedrockRuntimeClient({ region: process.env.REGION });

interface SubtitleMessage {
  type: 'subtitle' | 'error';
  originalText?: string;
  translatedText?: string;
  speaker?: string;
  timestamp: string;
  message?: string;
}

async function sendToClient(
  apigw: ApiGatewayManagementApiClient,
  connectionId: string,
  data: SubtitleMessage
): Promise<void> {
  try {
    await apigw.send(
      new PostToConnectionCommand({
        ConnectionId: connectionId,
        Data: Buffer.from(JSON.stringify(data)),
      })
    );
  } catch (err) {
    if (err instanceof GoneException) {
      console.warn(`Connection ${connectionId} is gone`);
    } else {
      throw err;
    }
  }
}

export const handler = async (
  event: APIGatewayProxyWebsocketEventV2
): Promise<{ statusCode: number; body: string }> => {
  const { connectionId } = event.requestContext;

  const apigw = new ApiGatewayManagementApiClient({
    endpoint: process.env.WS_ENDPOINT,
    region: process.env.REGION,
  });

  let body: {
    audioData?: string;
    meetingId?: string;
    speaker?: string;
  };

  try {
    body = JSON.parse(event.body ?? '{}');
  } catch {
    return { statusCode: 400, body: 'Invalid JSON' };
  }

  const { audioData, meetingId, speaker = 'remote' } = body;

  if (!audioData) {
    return { statusCode: 400, body: 'Missing audioData' };
  }

  try {
    // Step 1: STT via SageMaker Whisper
    const audioBuffer = Buffer.from(audioData, 'base64');
    const whisperRes = await sagemaker.send(
      new InvokeEndpointCommand({
        EndpointName: process.env.WHISPER_ENDPOINT,
        ContentType: 'audio/wav',
        Body: audioBuffer,
      })
    );

    const whisperResult = JSON.parse(
      Buffer.from(whisperRes.Body as Uint8Array).toString('utf-8')
    ) as { text?: string };

    const originalText = (whisperResult.text ?? '').trim();
    if (!originalText) {
      return { statusCode: 200, body: 'Empty transcription' };
    }

    // Step 2: Translate EN -> KO via Bedrock Claude
    const bedrockRes = await bedrock.send(
      new InvokeModelCommand({
        modelId: process.env.BEDROCK_MODEL_ID,
        contentType: 'application/json',
        accept: 'application/json',
        body: JSON.stringify({
          anthropic_version: 'bedrock-2023-05-31',
          max_tokens: 1024,
          messages: [
            {
              role: 'user',
              content: `Translate the following English text to Korean. Return only the translated text, no explanation.\n\n${originalText}`,
            },
          ],
        }),
      })
    );

    const bedrockResult = JSON.parse(
      Buffer.from(bedrockRes.body as Uint8Array).toString('utf-8')
    ) as { content?: Array<{ text: string }> };

    const translatedText = bedrockResult.content?.[0]?.text ?? '';

    // Step 3: Persist message to DynamoDB
    if (meetingId) {
      const message = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        speaker,
        originalText,
        translatedText,
        timestamp: new Date().toISOString(),
      };

      await ddb.send(
        new UpdateCommand({
          TableName: process.env.MEETINGS_TABLE,
          Key: { meetingId },
          UpdateExpression:
            'SET messages = list_append(if_not_exists(messages, :empty), :msg), #updatedAt = :ts',
          ExpressionAttributeNames: { '#updatedAt': 'updatedAt' },
          ExpressionAttributeValues: {
            ':msg': [message],
            ':empty': [],
            ':ts': new Date().toISOString(),
          },
        })
      );
    }

    // Step 4: Push subtitle back to client
    await sendToClient(apigw, connectionId, {
      type: 'subtitle',
      originalText,
      translatedText,
      speaker,
      timestamp: new Date().toISOString(),
    });

    return { statusCode: 200, body: 'OK' };
  } catch (err) {
    console.error('Error processing audio:', err);

    await sendToClient(apigw, connectionId, {
      type: 'error',
      message: 'Processing failed. Please try again.',
      timestamp: new Date().toISOString(),
    });

    return { statusCode: 500, body: 'Internal error' };
  }
};
