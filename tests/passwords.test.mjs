import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

function load(path, dependencies = {}) {
  const url = new URL(path, import.meta.url)
  const source = existsSync(url) ? readFileSync(url, 'utf8') : ''
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const exports = {}
  new Function('require', 'exports', outputText)((name) => {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`)
    return dependencies[name]
  }, exports)
  return exports
}

function setup({ signedIn = true, changeError, requestError, resetError } = {}) {
  const calls = []
  const user = {
    getSession: (callback) => callback(null, { isValid: () => true }),
    changePassword(current, next, callback) {
      calls.push({ action: 'change', current, next })
      callback(changeError, changeError ? undefined : 'SUCCESS')
    },
    signOut() {
      assert.fail('Changing a password must not interrupt the current meeting session')
    },
  }
  const api = load('../src/lib/cognito.ts', {
    'amazon-cognito-identity-js': {
      CognitoUserPool: class {
        getCurrentUser() {
          return signedIn ? user : null
        }
      },
      CognitoUser: class {
        constructor({ Username }) {
          this.email = Username
        }
        forgotPassword(callbacks) {
          calls.push({ action: 'request', email: this.email })
          if (requestError) callbacks.onFailure(requestError)
          else callbacks.onSuccess({ CodeDeliveryDetails: { DeliveryMedium: 'EMAIL' } })
        }
        confirmPassword(code, next, callbacks) {
          calls.push({ action: 'reset', email: this.email, code, next })
          if (resetError) callbacks.onFailure(resetError)
          else callbacks.onSuccess('SUCCESS')
        }
      },
    },
  })
  return { api, calls }
}

const policy = load('../src/lib/passwords.ts')
const awsError = (code) => Object.assign(new Error('AWS detail'), { code, name: code })

test('password change uses the signed-in account and preserves exact password input', async () => {
  const { api, calls } = setup()
  assert.equal(typeof api.changeAccountPassword, 'function')
  await api.changeAccountPassword(' current pass ', ' new pass ')
  assert.deepEqual(calls, [{ action: 'change', current: ' current pass ', next: ' new pass ' }])
})

test('a signed-out visitor cannot change an account password', async () => {
  const { api, calls } = setup({ signedIn: false })
  assert.equal(typeof api.changeAccountPassword, 'function')
  await assert.rejects(() => api.changeAccountPassword('current', 'new-password'), /로그인/)
  assert.deepEqual(calls, [])
})

test('an incorrect current password remains a failed change', async () => {
  const error = awsError('NotAuthorizedException')
  const { api } = setup({ changeError: error })
  assert.equal(typeof api.changeAccountPassword, 'function')
  await assert.rejects(() => api.changeAccountPassword('wrong', 'new-password'), error)
})

test('recovery works without a session and normalizes email but not the password', async () => {
  const { api, calls } = setup({ signedIn: false })
  assert.equal(typeof api.requestPasswordReset, 'function')
  assert.equal(typeof api.confirmPasswordReset, 'function')
  await api.requestPasswordReset(' person@example.com ')
  await api.confirmPasswordReset(' person@example.com ', ' 123456 ', ' new password ')
  assert.deepEqual(calls, [
    { action: 'request', email: 'person@example.com' },
    { action: 'reset', email: 'person@example.com', code: '123456', next: ' new password ' },
  ])
})

test('unknown-account recovery requests are indistinguishable from successful requests', async () => {
  const { api } = setup({ signedIn: false, requestError: awsError('UserNotFoundException') })
  assert.equal(typeof api.requestPasswordReset, 'function')
  assert.equal(await api.requestPasswordReset('unknown@example.com'), undefined)
})

test('delivery failures and expired reset codes are not reported as success', async () => {
  const delivery = awsError('CodeDeliveryFailureException')
  const expired = awsError('ExpiredCodeException')
  const { api } = setup({ requestError: delivery, resetError: expired })
  assert.equal(typeof api.requestPasswordReset, 'function')
  assert.equal(typeof api.confirmPasswordReset, 'function')
  await assert.rejects(() => api.requestPasswordReset('person@example.com'), delivery)
  await assert.rejects(
    () => api.confirmPasswordReset('person@example.com', '123456', 'new-password'),
    expired
  )
})

test('new passwords must match and meet the existing length policy', () => {
  assert.equal(typeof policy.validateNewPassword, 'function')
  assert.match(policy.validateNewPassword('short', 'short'), /8/)
  assert.match(policy.validateNewPassword('password-a', 'password-b'), /일치/)
  assert.equal(policy.validateNewPassword('abcdefgh', 'abcdefgh'), null)
  assert.equal(
    policy.validateNewPassword('different-long-password', 'different-long-password'),
    null
  )
})

test('password errors provide recovery steps without exposing AWS details', () => {
  assert.equal(typeof policy.passwordErrorMessage, 'function')
  assert.match(
    policy.passwordErrorMessage(awsError('NotAuthorizedException'), 'change'),
    /현재 비밀번호/
  )
  assert.match(policy.passwordErrorMessage(awsError('CodeMismatchException'), 'reset'), /코드/)
  assert.match(policy.passwordErrorMessage(awsError('ExpiredCodeException'), 'reset'), /만료/)
  assert.match(policy.passwordErrorMessage(awsError('LimitExceededException'), 'request'), /잠시/)
  assert.match(
    policy.passwordErrorMessage(awsError('InvalidPasswordException'), 'setup'),
    /비밀번호/
  )
  assert.doesNotMatch(
    policy.passwordErrorMessage(new Error('private AWS detail'), 'reset'),
    /private AWS/
  )
})
