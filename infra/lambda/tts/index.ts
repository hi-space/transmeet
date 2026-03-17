import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';
import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from '@aws-sdk/client-bedrock-runtime';
import {
  PollyClient,
  SynthesizeSpeechCommand,
  OutputFormat,
  VoiceId,
  Engine,
} from '@aws-sdk/client-polly';
import type { Readable } from 'stream';

const bedrock = new BedrockRuntimeClient({ region: process.env.REGION });
const polly = new PollyClient({ region: process.env.REGION });

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
};

async function streamToBuffer(stream: Readable): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as ArrayBuffer));
  }
  return Buffer.concat(chunks);
}

export const handler = async (
  event: APIGatewayProxyEvent
): Promise<APIGatewayProxyResult> => {
  let body: {
    text?: string;
    translateFirst?: boolean;
    voiceId?: string;
    engine?: string;
  };

  try {
    body = JSON.parse(event.body ?? '{}');
  } catch {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: 'Invalid JSON' }),
    };
  }

  const { text, translateFirst = true, voiceId = 'Ruth', engine = 'generative' } = body;

  if (!text?.trim()) {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: 'Missing text' }),
    };
  }

  try {
    let englishText = text;

    // Step 1: Translate KO -> EN via Bedrock (if requested)
    if (translateFirst) {
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
                content: `Translate the following Korean text to natural, fluent English. Return only the translated text.\n\n${text}`,
              },
            ],
          }),
        })
      );

      const bedrockResult = JSON.parse(
        Buffer.from(bedrockRes.body as Uint8Array).toString('utf-8')
      ) as { content?: Array<{ text: string }> };

      englishText = bedrockResult.content?.[0]?.text ?? text;
    }

    // Korean voice (Seoyeon) only supports neural — override generative/standard
    const pollyEngine: Engine =
      voiceId === 'Seoyeon' && engine !== 'neural' ? Engine.NEURAL : (engine as Engine);

    // Step 2: Synthesize speech with Polly TTS
    const pollyRes = await polly.send(
      new SynthesizeSpeechCommand({
        Text: englishText,
        OutputFormat: OutputFormat.MP3,
        VoiceId: (voiceId as VoiceId) ?? VoiceId.Ruth,
        Engine: pollyEngine,
      })
    );

    const audioBuffer = await streamToBuffer(pollyRes.AudioStream as Readable);
    const audioBase64 = audioBuffer.toString('base64');

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({
        audioData: audioBase64,
        translatedText: englishText,
        format: 'mp3',
      }),
    };
  } catch (err) {
    console.error('TTS error:', err);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: 'TTS synthesis failed' }),
    };
  }
};
