'use client'

import { useState } from 'react'
import type { CognitoUser } from 'amazon-cognito-identity-js'
import { signIn, completeNewPassword } from '@/lib/cognito'
import BrandLogo from '@/components/BrandLogo'
import Icon from '@/components/ui/Icon'

interface Props {
  onAuth: (user: CognitoUser) => void
}

export default function AuthScreen({ onAuth }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // new password challenge state
  const [pendingUser, setPendingUser] = useState<CognitoUser | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  async function handleLogin() {
    if (loading) return
    setError('')
    setLoading(true)
    try {
      const result = await signIn(email, password)
      if (result.type === 'newPasswordRequired') {
        setPendingUser(result.user)
      } else {
        onAuth(result.user)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '로그인에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  async function handleNewPassword() {
    if (!pendingUser || loading) return
    if (newPassword !== confirmPassword) {
      setError('비밀번호가 일치하지 않습니다.')
      return
    }
    setError('')
    setLoading(true)
    try {
      const user = await completeNewPassword(pendingUser, newPassword)
      onAuth(user)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '비밀번호 설정에 실패했습니다.')
    } finally {
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
            {pendingUser ? '새 비밀번호 설정' : '로그인'}
          </h1>
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
            {error && (
              <p
                id="auth-error"
                role="alert"
                className="rounded-lg bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-600 dark:bg-red-500/10 dark:text-red-400"
              >
                {error}
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
          </form>
        </section>
      </div>
    </div>
  )
}
