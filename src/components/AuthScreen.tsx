'use client'

import { useRef, useState } from 'react'
import type { CognitoUser } from 'amazon-cognito-identity-js'
import { signIn, completeNewPassword } from '@/lib/cognito'
import { passwordErrorMessage, validateNewPassword } from '@/lib/passwords'
import BrandLogo from '@/components/BrandLogo'
import PasswordResetForm from '@/components/PasswordResetForm'
import Icon from '@/components/ui/Icon'

interface Props {
  onAuth: (user: CognitoUser) => void
}

export default function AuthScreen({ onAuth }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [notice, setNotice] = useState('')
  const submitting = useRef(false)

  // new password challenge state
  const [pendingUser, setPendingUser] = useState<CognitoUser | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  async function handleLogin() {
    if (submitting.current) return
    submitting.current = true
    setError('')
    setNotice('')
    setLoading(true)
    try {
      const result = await signIn(email.trim(), password)
      if (result.type === 'newPasswordRequired') {
        setPendingUser(result.user)
        setPassword('')
      } else {
        onAuth(result.user)
      }
    } catch (e: unknown) {
      setError(passwordErrorMessage(e, 'login'))
    } finally {
      submitting.current = false
      setLoading(false)
    }
  }

  async function handleNewPassword() {
    if (!pendingUser || submitting.current) return
    const validation = validateNewPassword(newPassword, confirmPassword)
    if (validation) {
      setError(validation)
      return
    }
    submitting.current = true
    setError('')
    setLoading(true)
    try {
      const user = await completeNewPassword(pendingUser, newPassword)
      onAuth(user)
    } catch (e: unknown) {
      setError(passwordErrorMessage(e, 'setup'))
    } finally {
      submitting.current = false
      setLoading(false)
    }
  }

  return (
    <div className="h-dvh overflow-y-auto bg-canvas px-5 py-10">
      <div className="mx-auto flex min-h-full w-full max-w-sm flex-col justify-center">
        <div className="mb-7 flex justify-center">
          <BrandLogo size="auth" />
        </div>
        <section className="ui-surface p-6 sm:p-7">
          <h1 className="mb-6 text-lg font-semibold text-ink">
            {resetting ? '비밀번호 찾기' : pendingUser ? '새 비밀번호 설정' : '로그인'}
          </h1>
          {resetting ? (
            <PasswordResetForm
              initialEmail={email}
              onBack={() => setResetting(false)}
              onComplete={(address) => {
                setEmail(address)
                setPassword('')
                setResetting(false)
                setNotice('비밀번호가 재설정되었습니다. 새 비밀번호로 로그인해 주세요.')
              }}
            />
          ) : (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault()
                if (pendingUser) void handleNewPassword()
                else void handleLogin()
              }}
            >
              {pendingUser ? (
                <>
                  <label className="block text-xs font-medium text-ink" htmlFor="auth-new-password">
                    새 비밀번호
                    <input
                      id="auth-new-password"
                      name="newPassword"
                      type="password"
                      required
                      minLength={8}
                      maxLength={256}
                      value={newPassword}
                      onChange={(event) => setNewPassword(event.target.value)}
                      className="ui-input mt-2"
                      autoComplete="new-password"
                      aria-describedby={error ? 'auth-error' : undefined}
                    />
                  </label>
                  <label
                    className="block text-xs font-medium text-ink"
                    htmlFor="auth-confirm-password"
                  >
                    새 비밀번호 확인
                    <input
                      id="auth-confirm-password"
                      name="confirmPassword"
                      type="password"
                      required
                      maxLength={256}
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      className="ui-input mt-2"
                      autoComplete="new-password"
                      aria-describedby={error ? 'auth-error' : undefined}
                    />
                  </label>
                </>
              ) : (
                <>
                  <label className="block text-xs font-medium text-ink" htmlFor="auth-email">
                    이메일
                    <input
                      id="auth-email"
                      name="email"
                      type="email"
                      required
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      className="ui-input mt-2"
                      autoComplete="email"
                      placeholder="name@example.com"
                      spellCheck={false}
                      aria-describedby={error ? 'auth-error' : undefined}
                    />
                  </label>
                  <label className="block text-xs font-medium text-ink" htmlFor="auth-password">
                    비밀번호
                    <input
                      id="auth-password"
                      name="password"
                      type="password"
                      required
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      className="ui-input mt-2"
                      autoComplete="current-password"
                      aria-describedby={error ? 'auth-error' : undefined}
                    />
                  </label>
                </>
              )}
              {pendingUser && (
                <p className="text-xs leading-relaxed text-muted">
                  앞으로 사용할 비밀번호를 8자 이상으로 정해 주세요.
                </p>
              )}
              {error && (
                <p
                  id="auth-error"
                  role="alert"
                  className="rounded-lg bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-600 dark:bg-red-500/10 dark:text-red-400"
                >
                  {error}
                </p>
              )}
              {notice && (
                <p role="status" className="text-xs leading-relaxed text-muted">
                  {notice}
                </p>
              )}
              <button
                type="submit"
                disabled={loading}
                className="ui-button ui-button-primary min-h-11 w-full text-sm"
              >
                {loading && <Icon name="loader" className="h-4 w-4 animate-spin" />}
                {pendingUser
                  ? loading
                    ? '설정 중…'
                    : '비밀번호 설정'
                  : loading
                    ? '로그인 중…'
                    : '로그인'}
              </button>
              {!pendingUser && (
                <>
                  <button
                    type="button"
                    disabled={loading}
                    className="ui-button w-full"
                    onClick={() => {
                      setError('')
                      setNotice('')
                      setPassword('')
                      setResetting(true)
                    }}
                  >
                    비밀번호 찾기
                  </button>
                  <p className="text-center text-xs leading-relaxed text-muted">
                    초대받은 계정만 사용할 수 있습니다.
                  </p>
                </>
              )}
              {pendingUser && (
                <button
                  type="button"
                  disabled={loading}
                  className="ui-button w-full"
                  onClick={() => {
                    setPendingUser(null)
                    setNewPassword('')
                    setConfirmPassword('')
                    setError('')
                  }}
                >
                  로그인으로 돌아가기
                </button>
              )}
            </form>
          )}
        </section>
      </div>
    </div>
  )
}
