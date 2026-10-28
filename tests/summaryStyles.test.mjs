import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import { loadTypeScript } from './helpers/loadTypeScript.mjs'

class Command {
  constructor(input) {
    this.input = input
  }
}
class GetCommand extends Command {}
class PutCommand extends Command {}
class UpdateCommand extends Command {}
class DeleteCommand extends Command {}

process.env.SUMMARY_TEMPLATES_TABLE = 'templates-table'
process.env.SUMMARY_SNAPSHOTS_TABLE = 'snapshots-table'

function setup({ style, generatedStyle = 'detailed', owner = 'user-1', beforeModelReply, custom } = {}) {
  const templates = {
    'user-1|tpl-1': {
      userId: 'user-1', templateId: 'tpl-1', name: '주간 회의', kind: 'instruction',
      body: '섹션은 이슈 / 원인 / 조치', createdAt: '2026-10-28T00:00:00Z', updatedAt: '2026-10-28T01:00:00Z',
    },
    'user-2|tpl-9': {
      userId: 'user-2', templateId: 'tpl-9', name: '남의 양식', kind: 'instruction',
      body: '남의 본문', createdAt: '2026-10-28T00:00:00Z', updatedAt: '2026-10-28T00:00:00Z',
    },
  }
  const records = {
    'meeting-1': {
      meetingId: 'meeting-1', userId: owner, title: '출시 준비 회의',
      summary: '기존 상세 요약', summaryStyle: style, summaryGeneratedStyle: generatedStyle,
      messages: [{ id: 'message-1', originalText: '데이터 팀은 금요일까지 검증합니다.', speaker: 'speaker1' }],
    },
  }
  // 내 양식 사본은 회의 항목이 아니라 별도 테이블(meetingId 키)에 둔다 — 긴 회의의 400 KB 한도
  const snapshots = custom ? { 'meeting-1': { meetingId: 'meeting-1', userId: owner, ...custom } } : {}
  const deletes = []
  const updates = []
  const modelRequests = []
  const ddb = {
    async send(command) {
      const input = command.input
      if (command instanceof GetCommand) {
        if (input.TableName === 'templates-table') return { Item: templates[`${input.Key.userId}|${input.Key.templateId}`] }
        if (input.TableName === 'snapshots-table') return { Item: snapshots[input.Key.meetingId] }
        if (input.TableName !== process.env.MEETINGS_TABLE) return {}
        return { Item: records[input.Key.meetingId] }
      }
      if (command instanceof PutCommand) {
        if (input.TableName === 'snapshots-table') snapshots[input.Item.meetingId] = input.Item
        else records[input.Item.meetingId] = input.Item
        return {}
      }
      if (command instanceof DeleteCommand) {
        deletes.push(input)
        if (input.TableName === 'snapshots-table') delete snapshots[input.Key.meetingId]
        else if (input.TableName === process.env.MEETINGS_TABLE) delete records[input.Key.meetingId]
        return {}
      }
      assert.ok(command instanceof UpdateCommand)
      updates.push(input)
      const item = records[input.Key.meetingId]
      const [setPart, removePart] = input.UpdateExpression.split(/\s+REMOVE\s+/)
      for (const assignment of setPart.replace(/^SET /, '').split(',')) {
        const [field, parameter] = assignment.split('=').map((part) => part.trim())
        item[field] = input.ExpressionAttributeValues[parameter]
      }
      for (const field of (removePart ?? '').split(',').map((f) => f.trim()).filter(Boolean)) delete item[field]
      return {}
    },
  }
  const dependencies = {
    crypto: { randomUUID },
    '@aws-sdk/client-dynamodb': { DynamoDBClient: class {} },
    '@aws-sdk/lib-dynamodb': {
      DynamoDBDocumentClient: { from: () => ddb }, GetCommand, PutCommand, UpdateCommand, DeleteCommand,
    },
    '@aws-sdk/client-s3': {
      S3Client: class { async send() { return {} } },
      DeleteObjectCommand: Command, DeleteObjectsCommand: Command, ListObjectsV2Command: Command,
    },
    '@aws-sdk/client-bedrock-runtime': {
      ConverseCommand: Command,
      BedrockRuntimeClient: class {
        async send(command) {
          modelRequests.push(command.input)
          beforeModelReply?.(records['meeting-1'])
          return { output: { message: { content: [{ text: '생성한 요약' }] } } }
        }
      },
    },
  }
  const meetings = loadTypeScript('infra/lambda/meetings/index.ts', dependencies)
  const summarize = loadTypeScript('infra/lambda/summarize/index.ts', dependencies)
  const requestContext = { authorizer: { claims: { sub: 'user-1' } } }
  const event = (body) => ({
    pathParameters: { id: 'meeting-1' }, requestContext, body: JSON.stringify(body),
  })
  return {
    records, updates, modelRequests, templates, snapshots, deletes,
    get: () => meetings.handler({ ...event(), httpMethod: 'GET', resource: '/meetings/{id}' }),
    remove: () => meetings.handler({ ...event(), httpMethod: 'DELETE', resource: '/meetings/{id}' }),
    save: (summaryStyle, extra = {}) => meetings.handler({
      ...event({ summaryStyle, ...extra }), httpMethod: 'PUT', resource: '/meetings/{id}/summary-style',
    }),
    summarize: (body = {}) => summarize.handler({ ...event(body), httpMethod: 'POST' }),
  }
}

test('saving a meeting style preserves its existing summary and does not invoke the model', async () => {
  const session = setup()
  const result = await session.save('report')
  assert.equal(result.statusCode, 200)
  assert.deepEqual(JSON.parse(result.body), { meetingId: 'meeting-1', summaryStyle: 'report' })
  assert.equal(session.records['meeting-1'].summaryStyle, 'report')
  assert.equal(session.records['meeting-1'].summary, '기존 상세 요약')
  assert.equal(session.records['meeting-1'].summaryGeneratedStyle, 'detailed')
  assert.equal(session.modelRequests.length, 0)
})

test('invalid style selections cannot overwrite a saved preference', async () => {
  for (const invalid of [undefined, null, '', 'unknown', '__proto__', [], {}]) {
    const session = setup({ style: 'report' })
    const result = await session.save(invalid)
    assert.equal(result.statusCode, 400, JSON.stringify(invalid))
    assert.equal(session.records['meeting-1'].summaryStyle, 'report')
    assert.equal(session.updates.length, 0)
  }
})

test('style changes and summary generation retain meeting ownership checks', async () => {
  const session = setup({ owner: 'other-user' })
  assert.equal((await session.save('report')).statusCode, 404)
  assert.equal((await session.summarize({ summaryStyle: 'report' })).statusCode, 404)
  assert.equal(session.updates.length, 0)
  assert.equal(session.modelRequests.length, 0)
})

for (const [saved, requested, expected, heading] of [
  [undefined, undefined, 'detailed', '## 논의 흐름'],
  ['detailed', 'report', 'report', '## 향후 일정'],
  ['amazon', undefined, 'amazon', '## 리스크·미결 (Risks & Open Issues)'],
  ['report', 'decisions', 'decisions', '## 결정 기록'],
  ['report', 'detailed', 'detailed', '## 논의 흐름'],
  ['concise', undefined, 'detailed', '## 논의 흐름'],
  ['actions', 'actions', 'detailed', '## 논의 흐름'],
  ['report', undefined, 'report', '## 배경'],
  ['unknown', undefined, 'detailed', '## 논의 흐름'],
  ['report', '__proto__', 'detailed', '## 논의 흐름'],
]) {
  test(`REST summary resolves saved=${saved}, request=${requested} to ${expected}`, async () => {
    const session = setup({ style: saved })
    const result = await session.summarize({ summaryStyle: requested })
    assert.equal(result.statusCode, 200)
    assert.equal(JSON.parse(result.body).summaryGeneratedStyle, expected)
    assert.equal(session.records['meeting-1'].summaryGeneratedStyle, expected)
    const prompt = session.modelRequests[0].messages[0].content[0].text
    assert.ok(prompt.includes(heading))
    assert.ok(prompt.endsWith('\n---\n데이터 팀은 금요일까지 검증합니다.'))
    assert.ok(!prompt.includes('기존 상세 요약'))
    if (expected !== 'detailed') assert.ok(!prompt.includes('## 논의 흐름'))
  })
}

test('completing an older summary cannot overwrite a newly selected style', async () => {
  const session = setup({
    style: 'report',
    beforeModelReply: (meeting) => { meeting.summaryStyle = 'detailed' },
  })
  await session.summarize({ summaryStyle: 'report' })
  assert.equal(session.records['meeting-1'].summaryStyle, 'detailed')
  assert.equal(session.records['meeting-1'].summaryGeneratedStyle, 'report')
})

test('selecting a custom template copies it server-side and ignores client-sent body', async () => {
  const session = setup()
  const result = await session.save('custom', { templateId: 'tpl-1', body: '해킹', name: '가짜' })
  assert.equal(result.statusCode, 200)
  const expected = {
    templateId: 'tpl-1', name: '주간 회의', kind: 'instruction',
    body: '섹션은 이슈 / 원인 / 조치', templateUpdatedAt: '2026-10-28T01:00:00Z',
  }
  assert.deepEqual(JSON.parse(result.body), { meetingId: 'meeting-1', summaryStyle: 'custom', summaryCustom: expected })
  assert.deepEqual(session.snapshots['meeting-1'], { meetingId: 'meeting-1', userId: 'user-1', ...expected })
  assert.equal('summaryCustom' in session.records['meeting-1'], false)
  assert.equal(session.records['meeting-1'].summaryStyle, 'custom')
  assert.equal(session.modelRequests.length, 0)
})

test("selecting another user's or a missing template is 404 and leaves the meeting unchanged", async () => {
  for (const templateId of ['tpl-9', 'nope']) {
    const session = setup({ style: 'report' })
    assert.equal((await session.save('custom', { templateId })).statusCode, 404)
    assert.equal(session.records['meeting-1'].summaryStyle, 'report')
    assert.deepEqual(session.snapshots, {})
    assert.equal(session.updates.length, 0)
  }
})

test('custom without templateId is 400', async () => {
  const session = setup({ style: 'report' })
  for (const templateId of [undefined, '', 3]) {
    assert.equal((await session.save('custom', { templateId })).statusCode, 400)
  }
  assert.equal(session.updates.length, 0)
})

test('reselecting after an edit refreshes the snapshot', async () => {
  const session = setup()
  await session.save('custom', { templateId: 'tpl-1' })
  session.templates['user-1|tpl-1'] = { ...session.templates['user-1|tpl-1'], body: '고친 양식', updatedAt: '2026-10-28T02:00:00Z' }
  await session.save('report')
  await session.save('custom', { templateId: 'tpl-1' })
  assert.equal(session.snapshots['meeting-1'].body, '고친 양식')
  assert.equal(session.snapshots['meeting-1'].templateUpdatedAt, '2026-10-28T02:00:00Z')
})

const SNAPSHOT = {
  templateId: 'tpl-1', name: '주간 회의', kind: 'instruction',
  body: '섹션은 이슈 / 원인 / 조치', templateUpdatedAt: '2026-10-28T01:00:00Z',
}

test("selecting a template for someone else's meeting writes no snapshot", async () => {
  const session = setup({ owner: 'other-user' })
  assert.equal((await session.save('custom', { templateId: 'tpl-1' })).statusCode, 404)
  assert.deepEqual(session.snapshots, {})
})

test('GET meeting merges its snapshot as summaryCustom; deleting the meeting deletes the snapshot', async () => {
  const session = setup({ style: 'custom', custom: SNAPSHOT })
  const got = JSON.parse((await session.get()).body)
  assert.deepEqual(got.summaryCustom, SNAPSHOT)
  assert.equal((await session.remove()).statusCode, 204)
  assert.deepEqual(session.snapshots, {})
})

test('GET meeting on a builtin style does not attach a stale snapshot', async () => {
  const session = setup({ style: 'report', custom: SNAPSHOT })
  assert.equal(JSON.parse((await session.get()).body).summaryCustom, undefined)
})

test('REST summary with a custom snapshot uses it and stores the label', async () => {
  const session = setup({ style: 'custom', custom: SNAPSHOT })
  const result = await session.summarize()
  assert.equal(result.statusCode, 200)
  const prompt = session.modelRequests[0].messages[0].content[0].text
  assert.ok(prompt.includes('<사용자_양식>\n섹션은 이슈 / 원인 / 조치\n</사용자_양식>'))
  assert.equal(session.records['meeting-1'].summaryGeneratedStyle, 'custom')
  assert.equal(session.records['meeting-1'].summaryGeneratedLabel, '주간 회의')
  const body = JSON.parse(result.body)
  assert.equal(body.summaryGeneratedStyle, 'custom')
  assert.equal(body.summaryGeneratedLabel, '주간 회의')
})

test('REST summary with custom but no snapshot falls back to detailed and clears the label', async () => {
  const session = setup({ style: 'custom' })
  session.records['meeting-1'].summaryGeneratedLabel = '예전 양식'
  const result = await session.summarize({ summaryStyle: 'custom' })
  assert.equal(JSON.parse(result.body).summaryGeneratedStyle, 'detailed')
  assert.equal(JSON.parse(result.body).summaryGeneratedLabel, undefined)
  assert.ok(session.modelRequests[0].messages[0].content[0].text.includes('## 논의 흐름'))
  assert.equal(session.records['meeting-1'].summaryGeneratedStyle, 'detailed')
  assert.equal('summaryGeneratedLabel' in session.records['meeting-1'], false)
})

test('Python and REST build the same prompts, including the original transcript', () => {
  const { buildSummaryPrompt, SUMMARY_SYSTEM_PROMPT } = loadTypeScript('infra/lambda/shared/summaryPrompt.ts')
  const python = JSON.parse(execFileSync('python', ['-c', [
    'import json',
    'from app.summary_prompt import build_summary_prompt, SUMMARY_SYSTEM_PROMPT',
    'print(json.dumps({"system": SUMMARY_SYSTEM_PROMPT, "prompts": {s: build_summary_prompt("  주간 회의  ", "원본 발언", s) for s in ("detailed", "amazon", "decisions", "report")}}))',
  ].join('\n')], { cwd: fileURLToPath(new URL('../backend/', import.meta.url)), encoding: 'utf8' }))
  assert.equal(SUMMARY_SYSTEM_PROMPT, python.system)
  for (const [style, prompt] of Object.entries(python.prompts)) {
    assert.equal(buildSummaryPrompt('  주간 회의  ', '원본 발언', style), prompt)
  }
})

const CUSTOM = {
  templateId: 't1', name: '주간 회의', kind: 'instruction',
  body: '섹션은 이슈 / 원인 / 조치', templateUpdatedAt: '2026-10-28T00:00:00Z',
}

test('Python and REST build the same custom prompts and resolve styles the same way', () => {
  const { buildSummaryPrompt, resolveSummaryStyle } = loadTypeScript('infra/lambda/shared/summaryPrompt.ts')
  const example = { ...CUSTOM, kind: 'example', body: '## 이슈\n- A팀 3억' }
  const cases = [['custom', 'detailed', true], [null, 'custom', true], ['custom', 'custom', false],
    ['report', 'custom', true], ['bogus', 'custom', true]]
  const python = JSON.parse(execFileSync('python', ['-c', [
    'import json, sys',
    'from app.summary_prompt import build_summary_prompt, resolve_summary_style',
    'c = json.loads(sys.argv[1]); e = json.loads(sys.argv[2]); cases = json.loads(sys.argv[3])',
    'print(json.dumps({"instruction": build_summary_prompt("주간", "원본 발언", "custom", c),',
    '  "example": build_summary_prompt("주간", "원본 발언", "custom", e),',
    '  "resolved": [resolve_summary_style(r, s, c if has else None) for r, s, has in cases]}))',
  ].join('\n'), JSON.stringify(CUSTOM), JSON.stringify(example), JSON.stringify(cases)],
  { cwd: fileURLToPath(new URL('../backend/', import.meta.url)), encoding: 'utf8' }))
  assert.equal(buildSummaryPrompt('주간', '원본 발언', 'custom', CUSTOM), python.instruction)
  assert.equal(buildSummaryPrompt('주간', '원본 발언', 'custom', example), python.example)
  assert.deepEqual(
    cases.map(([r, s, has]) => resolveSummaryStyle(r ?? undefined, s, has ? CUSTOM : null)),
    python.resolved,
  )
  assert.deepEqual(python.resolved, ['custom', 'custom', 'detailed', 'report', 'detailed'])
})

test('custom style survives normalization and shows the template name', () => {
  const styles = loadTypeScript('src/lib/summaryStyles.ts')
  assert.equal(styles.normalizeSummaryStyle('custom'), 'custom')
  assert.equal(styles.isSummaryStyle('custom'), true)
  assert.equal(styles.isBuiltinSummaryStyle('custom'), false)
  assert.equal(styles.summaryStyleLabel('custom', '주간 회의'), '주간 회의')
  assert.equal(styles.summaryStyleLabel('custom'), '내 양식')
  assert.deepEqual({ ...styles.SUMMARY_TEMPLATE_LIMITS },
    { nameMax: 30, instructionMax: 6000, exampleMax: 12000, perUser: 10 })
})

test('parseSummaryCustom keeps only well-formed snapshots within limits', () => {
  const { parseSummaryCustom } = loadTypeScript('infra/lambda/shared/summaryPrompt.ts')
  assert.deepEqual(parseSummaryCustom(CUSTOM), CUSTOM)
  assert.equal(parseSummaryCustom({ ...CUSTOM, kind: 'example', body: 'a'.repeat(12000) }).kind, 'example')
  for (const bad of [null, 'x', { ...CUSTOM, kind: 'other' }, { ...CUSTOM, body: 'a'.repeat(6001) },
    { ...CUSTOM, kind: 'example', body: 'a'.repeat(12001) }, { ...CUSTOM, body: '  ' }, { ...CUSTOM, body: 3 }]) {
    assert.equal(parseSummaryCustom(bad), null, JSON.stringify(bad)?.slice(0, 60))
  }
})

test('loading old and malformed meeting styles uses the detailed default', () => {
  const { toMeeting } = loadTypeScript('src/lib/api.ts', { '@/lib/cognito': {} })
  for (const value of [undefined, null, 'unknown', '__proto__', {}]) {
    const meeting = toMeeting({
      meetingId: 'm1', title: '과거 회의', createdAt: '', summary: '기존 요약',
      summaryStyle: value, summaryGeneratedStyle: value,
    })
    assert.equal(meeting.summaryStyle, 'detailed')
    assert.equal(meeting.summaryGeneratedStyle, 'detailed')
  }
  const meeting = toMeeting({
    meetingId: 'm1', title: '다음 회의', createdAt: '', summary: '기존 요약',
    summaryStyle: 'report', summaryGeneratedStyle: 'detailed',
  })
  assert.equal(meeting.summaryStyle, 'report')
  assert.equal(meeting.summaryGeneratedStyle, 'detailed')
})

test('a stop-triggered summary waits for a pending style save even below the automatic threshold', async () => {
  const source = ts.createSourceFile(
    'page.tsx', readFileSync(new URL('../src/app/page.tsx', import.meta.url), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX
  )
  function find(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'handleSummarize') {
      return node.initializer.arguments[0]
    }
    return ts.forEachChild(node, find)
  }
  const { outputText } = ts.transpileModule(`const handler = ${find(source).getText(source)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  })
  let finishSave
  const pendingSave = new Promise((resolve) => { finishSave = resolve })
  const sent = []
  const scope = {
    activeMeetingId: 'm1',
    summaryStyleSavingRef: { current: new Map([['m1', pendingSave]]) },
    isSummarizingRef: { current: false },
    isSummarizingTimeoutRef: { current: null },
    summaryRequestRef: { current: null },
    summaryStreamsRef: { current: new Set() },
    meetingsRef: { current: [{ id: 'm1', summaryStyle: 'detailed', messages: [{ original: '새 발언' }] }] },
    normalizeSummaryStyle: loadTypeScript('src/lib/summaryStyles.ts').normalizeSummaryStyle,
    lastSummarizedCountRef: { current: { m1: 0 } },
    HAS_API: true, wsStatus: 'connected',
    sendSummarize: (...args) => { sent.push(args); return true },
    setIsSummarizing: () => {}, setMeetings: () => {},
    setTimeout: () => 1, clearTimeout: () => {},
  }
  const summarize = new Function(...Object.keys(scope), `${outputText}; return handler`)(...Object.values(scope))
  const pendingSummary = summarize()
  assert.equal(sent.length, 0)
  finishSave('report')
  await pendingSummary
  assert.equal(sent.length, 1)
  assert.equal(sent[0][0], 'm1')
  assert.equal(sent[0][2], 'report')
  assert.equal(sent[0][1][0].original, '새 발언')
})

test('toMeeting keeps a valid custom snapshot and label, and drops malformed snapshots', () => {
  const { toMeeting } = loadTypeScript('src/lib/api.ts', { '@/lib/cognito': {} })
  const base = { meetingId: 'm1', title: '회의', createdAt: '', summary: '요약', summaryStyle: 'custom' }
  const meeting = toMeeting({ ...base, summaryCustom: SNAPSHOT, summaryGeneratedStyle: 'custom', summaryGeneratedLabel: '주간 회의' })
  assert.equal(meeting.summaryStyle, 'custom')
  assert.deepEqual(meeting.summaryCustom, SNAPSHOT)
  assert.equal(meeting.summaryGeneratedStyle, 'custom')
  assert.equal(meeting.summaryGeneratedLabel, '주간 회의')
  for (const bad of [{ ...SNAPSHOT, kind: 'other' }, { ...SNAPSHOT, body: 3 }, 'x']) {
    assert.equal(toMeeting({ ...base, summaryCustom: bad }).summaryCustom, undefined)
  }
  const builtin = toMeeting({ ...base, summaryGeneratedStyle: 'report', summaryGeneratedLabel: '남은 라벨' })
  assert.equal(builtin.summaryGeneratedLabel, undefined)
})

test('a custom summary request sends only the style value, never the template body', async () => {
  const source = ts.createSourceFile(
    'page.tsx', readFileSync(new URL('../src/app/page.tsx', import.meta.url), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX
  )
  function find(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'handleSummarize') {
      return node.initializer.arguments[0]
    }
    return ts.forEachChild(node, find)
  }
  const { outputText } = ts.transpileModule(`const handler = ${find(source).getText(source)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  })
  const sent = []
  const scope = {
    activeMeetingId: 'm1',
    summaryStyleSavingRef: { current: new Map() },
    isSummarizingRef: { current: false },
    isSummarizingTimeoutRef: { current: null },
    summaryRequestRef: { current: null },
    summaryStreamsRef: { current: new Set() },
    meetingsRef: { current: [{ id: 'm1', summaryStyle: 'custom', summaryCustom: SNAPSHOT, messages: [{ original: '새 발언' }] }] },
    normalizeSummaryStyle: loadTypeScript('src/lib/summaryStyles.ts').normalizeSummaryStyle,
    lastSummarizedCountRef: { current: { m1: 0 } },
    HAS_API: true, wsStatus: 'connected',
    sendSummarize: (...args) => { sent.push(args); return true },
    setIsSummarizing: () => {}, setMeetings: () => {},
    setTimeout: () => 1, clearTimeout: () => {},
  }
  const summarize = new Function(...Object.keys(scope), `${outputText}; return handler`)(...Object.values(scope))
  await summarize()
  assert.equal(sent.length, 1)
  assert.equal(sent[0][2], 'custom')
  assert.ok(!JSON.stringify(sent).includes(SNAPSHOT.body))
})

test('templates load only once signed in, and again after signing in', () => {
  const source = ts.createSourceFile(
    'page.tsx', readFileSync(new URL('../src/app/page.tsx', import.meta.url), 'utf8'),
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX
  )
  let arg
  ;(function find(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'useSummaryTemplates') arg = node.arguments[0]
    ts.forEachChild(node, find)
  })(source)
  assert.ok(arg, 'useSummaryTemplates call')
  const enabled = (scope) =>
    new Function(...Object.keys(scope), `return (${arg.getText(source)})`)(...Object.values(scope))
  const base = { HAS_API: true, HAS_COGNITO: true, authLoading: false }
  assert.equal(enabled({ ...base, user: null }), false)
  assert.equal(enabled({ ...base, authLoading: true, user: null }), false)
  assert.equal(enabled({ ...base, user: { sub: 'u1' } }), true)
  assert.equal(enabled({ ...base, HAS_COGNITO: false, user: null }), true)
  assert.equal(enabled({ ...base, HAS_API: false, user: { sub: 'u1' } }), false)
})

test('example templates repeat the no-copy rule after the pasted sample', () => {
  const { buildSummaryPrompt } = loadTypeScript('infra/lambda/shared/summaryPrompt.ts')
  const prompt = buildSummaryPrompt('주간', '원문', 'custom', { ...CUSTOM, kind: 'example', body: '## 견본' })
  const after = prompt.slice(prompt.indexOf('</사용자_양식>'), prompt.indexOf('\n---\n'))
  assert.ok(after.includes('옮기지 않습니다'), after)
  const instruction = buildSummaryPrompt('주간', '원문', 'custom', CUSTOM)
  assert.ok(!instruction.slice(instruction.indexOf('</사용자_양식>')).includes('견본'))
})
