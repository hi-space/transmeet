/**
 * WebSocket audio handler
 * Flow: audio chunk -> Whisper STT -> Bedrock streaming translation -> WS response
 *
 * Message sequence per audio chunk:
 *   1. { type: "subtitle_stream", phase: "stt",         originalText }
 *   2. { type: "subtitle_stream", phase: "translating", partialTranslation } × N tokens
 *   3. { type: "subtitle_stream", phase: "done",        originalText, translatedText, detectedLanguage }
 *
 * Expected inbound message format:
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
  InvokeModelWithResponseStreamCommand,
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

interface StreamMessage {
  type: 'subtitle_stream';
  messageId: string;
  phase: 'stt' | 'translating' | 'done';
  speaker?: string;
  timestamp: string;
  originalText?: string;
  partialTranslation?: string;
  translatedText?: string;
  detectedLanguage?: 'ko' | 'en';
}

interface ErrorMessage {
  type: 'error';
  message: string;
  timestamp: string;
}

/**
 * Send a message to the WebSocket client.
 * Returns true if the connection is gone (client disconnected) so callers
 * can abort further processing early.
 */
async function sendToClient(
  apigw: ApiGatewayManagementApiClient,
  connectionId: string,
  data: StreamMessage | ErrorMessage
): Promise<boolean> {
  try {
    await apigw.send(
      new PostToConnectionCommand({
        ConnectionId: connectionId,
        Data: Buffer.from(JSON.stringify(data)),
      })
    );
    return false;
  } catch (err) {
    if (err instanceof GoneException) {
      console.warn(`Connection ${connectionId} is gone`);
      return true;
    }
    throw err;
  }
}

/** Normalize Whisper language codes to 'ko' | 'en' */
function normalizeLanguage(lang: string): 'ko' | 'en' {
  const l = lang.toLowerCase();
  if (l === 'ko' || l === 'korean' || l === 'kor') return 'ko';
  return 'en';
}

export const handler = async (
  wsEvent: APIGatewayProxyWebsocketEventV2
): Promise<{ statusCode: number; body: string }> => {
  const { connectionId } = wsEvent.requestContext;
  const timestamp = new Date().toISOString();
  const messageId = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  const apigw = new ApiGatewayManagementApiClient({
    endpoint: process.env.WS_ENDPOINT,
    region: process.env.REGION,
  });

  let body: {
    audioData?: string;
    meetingId?: string;
    speaker?: string;
    sourceLang?: string; // 'ko' | 'en' | 'auto' — from client settings
    targetLang?: string; // 'ko' | 'en' — from client settings
    modelId?: string; // Bedrock model override from client settings
  };
  try {
    body = JSON.parse(wsEvent.body ?? '{}');
  } catch {
    return { statusCode: 400, body: 'Invalid JSON' };
  }

  const {
    audioData,
    meetingId,
    speaker = 'remote',
    sourceLang: reqSourceLang,
    targetLang: reqTargetLang,
    modelId: reqModelId,
  } = body;

  const bedrockModelId = reqModelId || process.env.BEDROCK_MODEL_ID;
  if (!audioData) {
    return { statusCode: 400, body: 'Missing audioData' };
  }

  try {
    // ── Step 1: STT via SageMaker Whisper ─────────────────────────────────────
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
    ) as { text?: string | string[]; language?: string };

    const rawText = whisperResult.text;
    const originalText = (
      Array.isArray(rawText) ? rawText.join(' ') : rawText ?? ''
    ).trim();
    if (!originalText) {
      return { statusCode: 200, body: 'Empty transcription' };
    }

    // ── Hallucination filter ───────────────────────────────────────────────
    // Only filter unambiguous Whisper artifacts (not real speech fragments)
    const HALLUCINATION_PATTERNS = [
      /^(uh\.?|um\.?|hmm+\.?)$/i,
      /^thanks\s+for\s+watching\.?$/i,
      /^(please\s+)?subscribe\.?$/i,
      /^(like\s+and\s+subscribe\.?)$/i,
      /^\[.*\]$/, // [Music], [Silence], [Applause], etc.
      /^\(.*\)$/, // (Music), (silence), etc.
    ];
    const isHallucination = HALLUCINATION_PATTERNS.some((p) =>
      p.test(originalText)
    );
    if (isHallucination) {
      console.log('[ws-audio] Hallucination filtered:', JSON.stringify(originalText));
      return { statusCode: 200, body: 'Hallucination filtered' };
    }

    // Client override > Whisper detection > default 'en'
    const detectedLanguage: 'ko' | 'en' =
      reqSourceLang && reqSourceLang !== 'auto'
        ? normalizeLanguage(reqSourceLang)
        : normalizeLanguage(whisperResult.language ?? '');

    const translationTarget: 'ko' | 'en' = reqTargetLang
      ? normalizeLanguage(reqTargetLang)
      : detectedLanguage === 'ko'
        ? 'en'
        : 'ko';

    const sourceLang = detectedLanguage === 'ko' ? 'Korean' : 'English';
    const targetLang = translationTarget === 'ko' ? 'Korean' : 'English';

    // ── Step 2: Push STT result immediately ───────────────────────────────────
    const goneAfterStt = await sendToClient(apigw, connectionId, {
      type: 'subtitle_stream',
      messageId,
      phase: 'stt',
      speaker,
      originalText,
      timestamp,
    });
    if (goneAfterStt) return { statusCode: 200, body: 'Connection gone' };

    // ── Step 3: Stream translation via Bedrock ────────────────────────────────
    const streamRes = await bedrock.send(
      new InvokeModelWithResponseStreamCommand({
        modelId: bedrockModelId,
        contentType: 'application/json',
        accept: 'application/json',
        body: JSON.stringify({
          anthropic_version: 'bedrock-2023-05-31',
          max_tokens: 1024,
          messages: [
            {
              role: 'user',
              content:
                `Translate the following ${sourceLang} text to ${targetLang}. ` +
                `Output only the translated text, no explanations, no quotes.\n\n` +
                `Text: ${originalText}`,
            },
          ],
        }),
      })
    );

    let translatedText = '';
    if (streamRes.body) {
      for await (const streamChunk of streamRes.body) {
        if (!streamChunk.chunk?.bytes) continue;
        const parsed = JSON.parse(
          Buffer.from(streamChunk.chunk.bytes).toString('utf-8')
        ) as { type?: string; delta?: { type?: string; text?: string } };

        if (
          parsed.type === 'content_block_delta' &&
          parsed.delta?.type === 'text_delta' &&
          parsed.delta.text
        ) {
          translatedText += parsed.delta.text;
          const gone = await sendToClient(apigw, connectionId, {
            type: 'subtitle_stream',
            messageId,
            phase: 'translating',
            speaker,
            originalText,
            partialTranslation: translatedText,
            timestamp,
          });
          if (gone) return { statusCode: 200, body: 'Connection gone' };
        }
      }
    }

    // ── Step 4: Push final completed subtitle ─────────────────────────────────
    await sendToClient(apigw, connectionId, {
      type: 'subtitle_stream',
      messageId,
      phase: 'done',
      speaker,
      originalText,
      translatedText,
      detectedLanguage,
      timestamp,
    });

    // ── Step 5: Persist to DynamoDB ───────────────────────────────────────────
    if (meetingId) {
      await ddb.send(
        new UpdateCommand({
          TableName: process.env.MEETINGS_TABLE,
          Key: { meetingId },
          UpdateExpression:
            'SET messages = list_append(if_not_exists(messages, :empty), :msg), #updatedAt = :ts',
          ExpressionAttributeNames: { '#updatedAt': 'updatedAt' },
          ExpressionAttributeValues: {
            ':msg': [
              {
                id: messageId,
                speaker,
                originalText,
                translatedText,
                detectedLanguage,
                timestamp,
              },
            ],
            ':empty': [],
            ':ts': timestamp,
          },
        })
      );
    }

    return { statusCode: 200, body: 'OK' };
  } catch (err) {
    console.error('Error processing audio:', err);
    await sendToClient(apigw, connectionId, {
      type: 'error',
      message: 'Processing failed. Please try again.',
      timestamp,
    });
    return { statusCode: 500, body: 'Internal error' };
  }
};
