import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';
import {
  BedrockRuntimeClient,
  ConverseCommand,
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
    modelId?: string;
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

  const { text, translateFirst = true, voiceId = 'Ruth', engine = 'generative', modelId } = body;

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
        new ConverseCommand({
          modelId: modelId ?? process.env.BEDROCK_MODEL_ID!,
          messages: [
            {
              role: 'user',
              content: [{ text: `Translate the following Korean text to natural, fluent English. Return only the translated text.\n\n${text}` }],
            },
          ],
          inferenceConfig: { maxTokens: 1024 },
        })
      );

      englishText = bedrockRes.output?.message?.content?.[0]?.text ?? text;
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
