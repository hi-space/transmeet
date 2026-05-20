import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import {
  DynamoDBDocumentClient,
  GetCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb';
import {
  BedrockRuntimeClient,
  ConverseCommand,
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

const DEFAULT_TITLE_PREFIX = 'Meeting ';

async function maybeGenerateTitle(args: {
  meetingId: string;
  currentTitle: string;
  messages: MeetingMessage[];
}): Promise<string | null> {
  const { meetingId, currentTitle, messages } = args;
  if (!currentTitle.startsWith(DEFAULT_TITLE_PREFIX)) return null;
  if (messages.length === 0) return null;

  try {
    const head = messages
      .slice(0, 20)
      .map((m) => m.originalText)
      .join('\n');

    const titleModelId = process.env.BEDROCK_MODEL_ID ?? '';
    const res = await bedrock.send(
      new ConverseCommand({
        modelId: titleModelId,
        messages: [
          {
            role: 'user',
            content: [
              {
                text: `다음 회의 내용을 보고 짧은 제목을 생성하세요 (10-20자, 한국어). 제목만 출력하세요.\n\n${head}`,
              },
            ],
          },
        ],
        inferenceConfig: { maxTokens: 64 },
      })
    );

    const title = (res.output?.message?.content?.[0]?.text ?? '').trim();
    if (!title) return null;

    await ddb.send(
      new UpdateCommand({
        TableName: process.env.MEETINGS_TABLE,
        Key: { meetingId },
        UpdateExpression: 'SET title = :t, updatedAt = :u',
        ExpressionAttributeValues: {
          ':t': title,
          ':u': new Date().toISOString(),
        },
      })
    );
    return title;
  } catch (err) {
    console.error('Auto-title generation failed:', err);
    return null;
  }
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

    // Build transcript for Claude (original text only — speaker not differentiated)
    const transcript = messages.map((m) => m.originalText).join('\n');

    const modelId =
      process.env.SUMMARIZE_MODEL_ID ?? process.env.BEDROCK_MODEL_ID ?? '';

    // Stage A — extract a structural outline of the meeting.
    // The outline is not shown to the user; it guides Stage B's coverage.
    const outlineSystem = `You are a meeting analyst. Extract a structural outline of a meeting transcript so a downstream writer can produce a thorough summary with even depth across topics.`;

    const outlinePrompt = `Read the meeting transcript below and produce a compact Korean outline. The transcript does not separate speakers — treat it as a flat sequence of utterances. Output only the outline — no preamble, no closing remarks.

Format:

## 토픽
- 토픽1: 한 줄 요약
- 토픽2: 한 줄 요약
(회의에서 실제로 다뤄진 모든 주요 주제. 누락 없이.)

## 결정 사항
- 결정1
- 결정2

## 액션 아이템
- 항목 — 담당자(언급된 경우) / 기한(언급된 경우)

## 미해결 이슈
- 항목

## 기술 디테일 / 구체 정보
- 회의에서 언급된 구체적 수치, 명령어, 코드 스니펫, 시스템·서비스명, 라이브러리·버전, API·엔드포인트, 설정값, 에러 메시지, 일정·날짜, 링크 등 모든 비-개념적 사실
- 빠짐없이 한 줄씩 나열 (이 섹션은 양이 많아도 됨)

각 항목은 한 줄. 사실에 충실하게, 추측 금지.

---
${transcript}`;

    const outlineRes = await bedrock.send(
      new ConverseCommand({
        modelId,
        system: [{ text: outlineSystem }],
        messages: [{ role: 'user', content: [{ text: outlinePrompt }] }],
        inferenceConfig: { maxTokens: 8192 },
        additionalModelRequestFields: {
          thinking: { type: 'adaptive' },
          output_config: { effort: 'low' },
        },
      })
    );

    const outline =
      outlineRes.output?.message?.content?.find((b) => b.text)?.text ?? '';

    // Stage B — write the detailed summary using transcript as the source of
    // truth and the outline as a structural guide for even coverage.
    const systemPrompt = `You are a professional meeting summarizer. Produce thorough, well-structured Korean summaries that preserve nuance, reasoning, and context — not just conclusions. You are given the original transcript (the source of truth) and a pre-extracted outline (a coverage guide). Ground every claim in the transcript; do not invent details that are not present.`;

    const userPrompt = `다음은 회의 전사본과 미리 추출된 구조 outline입니다. 전사본은 발언자를 구분하지 않은 평문 시퀀스입니다 — "누가 말했는지"는 알 수 없으니 발언자 단위 분석은 하지 마세요. Outline은 어떤 주제·결정·액션·기술 디테일을 빠뜨리지 말아야 하는지 알려주는 **목차 가이드**일 뿐, 사실의 출처는 항상 전사본입니다. Outline에 포함된 모든 항목을 빠짐없이 다루되, 실제 서술의 근거는 전사본에서 가져오세요. Outline에 없더라도 전사본에서 의미있는 내용을 발견하면 추가하세요.

회의를 놓친 사람이 무엇이 논의되었고 *왜* 그렇게 결정되었는지 이해할 수 있도록, 단순한 결론 나열이 아니라 맥락·논거·반대 의견·트레이드오프까지 풍부하게 서술하세요. 특히 **기술적·구체적 디테일은 절대 압축하거나 일반화하지 말고 원문에 등장한 그대로 보존**하세요 (수치, 코드, 명령어, 시스템·서비스명, 라이브러리·버전, API·엔드포인트, 설정값, 에러 메시지, 일정·날짜, 링크 등).

다음 구조를 사용하세요:

## 개요
2-3문장으로 회의의 목적과 전체 흐름 요약.

## 핵심 메시지
회의에서 가장 중요한 결론 또는 합의 사항. 각 항목은 결정 그 자체뿐 아니라 그렇게 결정된 이유까지 함께 서술.

## 주요 논의 사항
주제별로 묶어서 정리 (outline의 토픽들을 모두 포함). 각 주제마다:
- 어떤 맥락/문제에서 시작되었는지
- 어떤 의견·대안·우려가 오갔는지 (찬반, 트레이드오프)
- 어떻게 정리되었는지 (또는 미결 상태인지)
- **관련된 기술 디테일·수치·고유명사를 본문에 그대로 포함** (예: "CDN TTL 60s → 300s로 상향", "claude-opus-4-7 모델 사용", "p99 latency 280ms")

## 기술 디테일 / 구체 정보
회의에서 언급된 모든 비-개념적 사실을 카테고리별로 정리. 양이 많아도 좋음. 다음과 같이 그룹핑:
- **수치·지표**: 측정값, 임계치, 비용, 토큰 수, 시간 등
- **시스템·서비스·도구**: 언급된 서비스명, 인프라, 라이브러리, 버전
- **코드·명령어·설정**: 코드 스니펫, CLI 명령, 환경변수, 설정 파일 키 — \`코드 블록\`으로 표기
- **API·엔드포인트·경로**: URL, 엔드포인트, 파일 경로
- **에러·로그**: 언급된 에러 메시지, 로그 라인
- **일정·날짜·링크**: 데드라인, 회의 일정, 참조 링크
해당 카테고리에 내용이 없으면 그 카테고리는 생략.

## 결정 사항 및 액션 아이템
- 확정된 결정: 무엇을, 언제까지
- 후속 작업: 담당자/기한이 명시되었으면 그대로, 아니면 "담당자 미정" 등으로 표시

## 미해결 이슈 / 후속 논의 필요
다음 회의로 넘어간 항목, 추가 정보가 필요한 항목, 의견이 갈린 채 마무리된 항목.

## 상세 노트
위 카테고리에 깔끔히 들어가지 않지만 기록할 가치가 있는 배경 정보, 참고 자료, 사이드 코멘트 등.

서술 가이드:
- 단순 요약보다 맥락과 추론을 보존할 것
- **기술 디테일은 일반화·축약 금지** — 원문 표현 그대로
- 발언자를 추측하거나 "A 의견 / B 의견" 같은 가상의 화자 분리는 하지 말 것 (전사본에 화자 정보 없음)
- 전사본에 없는 내용은 절대 만들어내지 말 것
- 마크다운 형식 사용 (## 헤딩, 불릿, **강조**, \`인라인 코드\`, \`\`\`코드 블록\`\`\`)
- 누락보다 풍부함을 우선
- Outline은 출력하지 말고 최종 요약문만 출력

---
[OUTLINE — 구조 가이드]
${outline}

---
[TRANSCRIPT — 사실의 출처]
${transcript}`;

    const bedrockRes = await bedrock.send(
      new ConverseCommand({
        modelId,
        system: [{ text: systemPrompt }],
        messages: [{ role: 'user', content: [{ text: userPrompt }] }],
        inferenceConfig: { maxTokens: 131072 },
        additionalModelRequestFields: {
          thinking: { type: 'adaptive' },
          output_config: { effort: 'high' },
        },
      })
    );

    const summary =
      bedrockRes.output?.message?.content?.find((b) => b.text)?.text ?? '';

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

    const newTitle = await maybeGenerateTitle({
      meetingId,
      currentTitle: (result.Item.title as string | undefined) ?? '',
      messages,
    });

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ summary, meetingId, ...(newTitle ? { title: newTitle } : {}) }),
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
