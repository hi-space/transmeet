import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const { outputText } = ts.transpileModule(
  readFileSync(new URL('../src/lib/cognito.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }
)

function setup({ verified = false, tokenVerified = verified, signedIn = true, codeError } = {}) {
  const calls = []
  const events = []
  const email = 'person@example.com'
  const makeSession = (isVerified) => ({
    isValid: () => true,
    getRefreshToken: () => 'refresh-token',
    getIdToken: () => ({
      decodePayload: () => ({ email, email_verified: isVerified }),
      getJwtToken: () => (isVerified ? 'verified-id-token' : 'unverified-id-token'),
    }),
  })
  let session = makeSession(tokenVerified)
  const user = {
    getSession(callback) {
      callback(null, session)
    },
    getSignInUserSession: () => session,
    getUserAttributes(callback) {
      callback(
        null,
        Object.entries({ email, email_verified: String(verified) }).map(([name, value]) => ({
          getName: () => name,
          getValue: () => value,
        }))
      )
    },
    getAttributeVerificationCode(name, callbacks) {
      calls.push({ action: 'send-code', name })
      callbacks.onSuccess('SUCCESS')
    },
    verifyAttribute(name, code, callbacks) {
      calls.push({ action: 'verify-code', name, code })
      if (codeError) return callbacks.onFailure(codeError)
      verified = true
      callbacks.onSuccess('SUCCESS')
    },
    refreshSession(token, callback) {
      calls.push({ action: 'refresh', token })
      session = makeSession(verified)
      callback(null, session)
    },
  }
  const exports = {}
  new Function('require', 'exports', 'window', 'CustomEvent', outputText)(
    (name) => {
      assert.equal(name, 'amazon-cognito-identity-js')
      return {
        CognitoUserPool: class {
          getCurrentUser() {
            return signedIn ? user : null
          }
        },
      }
    },
    exports,
    { dispatchEvent: (event) => events.push(event) },
    class {
      constructor(type, { detail }) {
        this.type = type
        this.detail = detail
      }
    }
  )
  return { api: exports, calls, events, email }
}

test('an unverified account can inspect its own email before requesting delivery setup', async () => {
  const { api, email, calls } = setup()
  assert.deepEqual(await api.getAccountEmail(), { email, verified: false })
  assert.deepEqual(calls, [])
})

test('requesting verification sends the code for the signed-in account email', async () => {
  const { api, calls } = setup()
  await api.sendAccountEmailVerificationCode()
  assert.deepEqual(calls, [{ action: 'send-code', name: 'email' }])
})

test('confirming the account email refreshes tokens before notifying an existing connection', async () => {
  const { api, calls, events } = setup()
  await api.verifyAccountEmailCode('123456')
  assert.deepEqual(calls, [
    { action: 'verify-code', name: 'email', code: '123456' },
    { action: 'refresh', token: 'refresh-token' },
  ])
  assert.deepEqual(
    events.map(({ type, detail }) => ({ type, detail })),
    [{ type: api.AUTH_SESSION_REFRESHED_EVENT, detail: 'verified-id-token' }]
  )
  assert.equal((await api.getAccountEmail()).verified, true)
})

test('a wrong verification code leaves the session unchanged and can be retried', async () => {
  const error = Object.assign(new Error('Incorrect code'), { name: 'CodeMismatchException' })
  const { api, calls, events } = setup({ codeError: error })
  await assert.rejects(() => api.verifyAccountEmailCode('000000'), error)
  assert.deepEqual(calls, [{ action: 'verify-code', name: 'email', code: '000000' }])
  assert.deepEqual(events, [])
})

test('verification in another tab refreshes stale cached claims when settings is reopened', async () => {
  const { api, calls, events, email } = setup({ verified: true, tokenVerified: false })
  assert.deepEqual(await api.getAccountEmail(), { email, verified: true })
  assert.deepEqual(calls, [{ action: 'refresh', token: 'refresh-token' }])
  assert.equal(events[0].detail, 'verified-id-token')
})

test('already-refreshed shared tokens are announced to this tab without another refresh request', async () => {
  const { api, calls, events, email } = setup({ verified: true, tokenVerified: true })
  assert.deepEqual(await api.getAccountEmail(), { email, verified: true })
  assert.deepEqual(calls, [])
  assert.equal(events[0]?.type, api.AUTH_SESSION_REFRESHED_EVENT)
  assert.equal(events[0]?.detail, 'verified-id-token')
})

test('a signed-out visitor cannot request an account verification email', async () => {
  const { api, calls } = setup({ signedIn: false })
  await assert.rejects(() => api.sendAccountEmailVerificationCode(), /로그인/)
  assert.deepEqual(calls, [])
})
