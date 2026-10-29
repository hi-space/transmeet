'use client'

import { useEffect, useRef, useState } from 'react'
import { confirmPasswordReset, requestPasswordReset } from '@/lib/cognito'
import { passwordErrorMessage, validateNewPassword } from '@/lib/passwords'
import Icon from '@/components/ui/Icon'

interface Props {
  initialEmail: string
  onBack: () => void
  onComplete: (email: string) => void
}

export default function PasswordResetForm({ initialEmail, onBack, onComplete }: Props) {
  const [email, setEmail] = useState(initialEmail)
  const [sent, setSent] = useState(false)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pending = useRef(false)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  async function sendCode() {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await requestPasswordReset(email)
      if (!mounted.current) return
      setSent(true)
      setCode('')
      setNotice('등록되고 이메일 인증이 완료된 계정이라면 인증 코드를 보냈습니다.')
    } catch (cause) {
      if (mounted.current) setError(passwordErrorMessage(cause, 'request'))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function resetPassword() {
    if (pending.current) return
    const validation = validateNewPassword(password, confirmation)
    if (validation) {
      setError(validation)
      return
    }
    pending.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await confirmPasswordReset(email, code, password)
      if (!mounted.current) return
      setPassword('')
      setConfirmation('')
      onComplete(email.trim())
    } catch (cause) {
      if (mounted.current) setError(passwordErrorMessage(cause, 'reset'))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        void (sent ? resetPassword() : sendCode())
      }}
    >
      <p className="text-xs leading-relaxed text-muted">
        {sent
          ? `${email.trim()}의 메일함에서 인증 코드를 확인하고 새 비밀번호를 정해 주세요.`
          : '초대받은 이메일을 입력해 주세요. 인증된 이메일로 비밀번호를 재설정할 수 있습니다.'}
      </p>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        {!sent ? (
          <label className="block text-xs font-medium text-ink" htmlFor="reset-email">
            이메일
            <input
              id="reset-email"
              type="email"
              name="email"
              required
              autoComplete="email"
              spellCheck={false}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="ui-input mt-2"
            />
          </label>
        ) : (
          <>
            <label className="block text-xs font-medium text-ink" htmlFor="reset-code">
              인증 코드
              <input
                id="reset-code"
                name="code"
                required
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                className="ui-input mt-2"
                aria-describedby={error ? 'reset-error' : undefined}
              />
            </label>
            <label className="block text-xs font-medium text-ink" htmlFor="reset-password">
              새 비밀번호
              <input
                id="reset-password"
                type="password"
                name="newPassword"
                required
                minLength={8}
                maxLength={256}
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="ui-input mt-2"
                aria-describedby="reset-password-hint"
              />
            </label>
            <p id="reset-password-hint" className="text-xs text-muted">
              8자 이상 입력해 주세요.
            </p>
            <label className="block text-xs font-medium text-ink" htmlFor="reset-confirmation">
              새 비밀번호 확인
              <input
                id="reset-confirmation"
                type="password"
                name="confirmPassword"
                required
                maxLength={256}
                autoComplete="new-password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="ui-input mt-2"
                aria-describedby={error ? 'reset-error' : undefined}
              />
            </label>
          </>
        )}
        {error && (
          <p
            id="reset-error"
            role="alert"
            className="text-xs leading-relaxed text-red-600 dark:text-red-400"
          >
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="text-xs leading-relaxed text-muted">
            {notice}
          </p>
        )}
        <button type="submit" className="ui-button ui-button-primary min-h-11 w-full">
          {busy && <Icon name="loader" className="h-4 w-4 animate-spin" />}
          {busy ? '처리 중…' : sent ? '비밀번호 재설정' : '인증 코드 받기'}
        </button>
        {sent && (
          <div className="space-y-2">
            <p className="text-xs leading-relaxed text-muted">
              메일이 오지 않으면 스팸함을 확인해 주세요. 이메일 인증을 하지 못했거나 초대가
              만료되었다면 관리자에게 문의해 주세요.
            </p>
            <button
              type="button"
              onClick={() => void sendCode()}
              className="ui-button ui-button-secondary w-full"
            >
              코드 다시 받기
            </button>
          </div>
        )}
        <button type="button" onClick={onBack} className="ui-button w-full">
          로그인으로 돌아가기
        </button>
      </fieldset>
    </form>
  )
}
