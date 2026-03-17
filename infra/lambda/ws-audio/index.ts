/**
 * WebSocket audio handler
 * Flow: audio chunk -> Transcribe Streaming STT -> Bedrock streaming translation -> WS response
 *
 * Message sequence per audio chunk:
 *   1. { type: "subtitle_stream", phase: "stt",         originalText, speaker }
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
  TranscribeStreamingClient,
  StartStreamTranscriptionCommand,
} from '@aws-sdk/client-transcribe-streaming';
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
const transcribeClient = new TranscribeStreamingClient({ region: process.env.REGION });
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

/**
 * Transcribe a single WAV buffer (16kHz, 16-bit PCM, mono) via Transcribe Streaming.
 * Returns the final transcript text and the first detected speaker ID.
 */
async function transcribeAudio(
  wavBuffer: Buffer
): Promise<{ text: string; speakerId?: string }> {
  // Strip 44-byte WAV header to send raw PCM to Transcribe
  const pcmData = wavBuffer.subarray(44);

  // Split PCM into 100ms chunks: 16kHz * 2 bytes/sample * 0.1s = 3200 bytes
  async function* audioStream() {
    const chunkSize = 3200;
    for (let i = 0; i < pcmData.length; i += chunkSize) {
      yield { AudioEvent: { AudioChunk: pcmData.subarray(i, i + chunkSize) } };
    }
  }

  const response = await transcribeClient.send(
    new StartStreamTranscriptionCommand({
      LanguageCode: 'en-US',
      MediaEncoding: 'pcm',
      MediaSampleRateHertz: 16000,
      ShowSpeakerLabel: true,
      AudioStream: audioStream(),
    })
  );

  let finalText = '';
  let speakerId: string | undefined;

  if (response.TranscriptResultStream) {
    for await (const event of response.TranscriptResultStream) {
      const results = event.TranscriptEvent?.Transcript?.Results;
      if (!results) continue;
      for (const result of results) {
        if (result.IsPartial) continue;
        const alt = result.Alternatives?.[0];
        if (!alt) continue;
        if (alt.Transcript) finalText = alt.Transcript;
        if (!speakerId) {
          for (const item of alt.Items ?? []) {
            if (item.Speaker) {
              speakerId = item.Speaker;
              break;
            }
          }
        }
      }
    }
  }

  return { text: finalText.trim(), speakerId };
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
    targetLang: reqTargetLang,
    modelId: reqModelId,
  } = body;

  const bedrockModelId = reqModelId || process.env.BEDROCK_MODEL_ID;
  if (!audioData) {
    return { statusCode: 400, body: 'Missing audioData' };
  }

  try {
    // ── Step 1: STT via AWS Transcribe Streaming ───────────────────────────────
    const audioBuffer = Buffer.from(audioData, 'base64');
    const { text: originalText, speakerId } = await transcribeAudio(audioBuffer);

    if (!originalText) {
      return { statusCode: 200, body: 'Empty transcription' };
    }

    // Use Transcribe speaker ID when available, fall back to client-supplied speaker
    const effectiveSpeaker = speakerId ?? speaker;

    // Transcribe input is always English; target translation is Korean by default
    const detectedLanguage: 'ko' | 'en' = 'en';
    const translationTarget: 'ko' | 'en' = reqTargetLang === 'en' ? 'en' : 'ko';
    const sourceLang = 'English';
    const targetLang = translationTarget === 'ko' ? 'Korean' : 'English';

    // ── Step 2: Push STT result immediately ───────────────────────────────────
    const goneAfterStt = await sendToClient(apigw, connectionId, {
      type: 'subtitle_stream',
      messageId,
      phase: 'stt',
      speaker: effectiveSpeaker,
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
            speaker: effectiveSpeaker,
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
      speaker: effectiveSpeaker,
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
                speaker: effectiveSpeaker,
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
