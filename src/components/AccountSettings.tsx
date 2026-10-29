'use client'

import { useEffect, useRef, useState } from 'react'
import {
  changeAccountPassword,
  getAccountEmail,
  sendAccountEmailVerificationCode,
  verifyAccountEmailCode,
} from '@/lib/cognito'
import { passwordErrorMessage, validateNewPassword } from '@/lib/passwords'
import Icon from '@/components/ui/Icon'

function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState('')
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

  async function changePassword() {
    if (pending.current) return
    setNotice('')
    const validation = validateNewPassword(password, confirmation)
    if (validation) {
      setError(validation)
      return
    }
    pending.current = true
    setBusy(true)
    setError('')
    try {
      await changeAccountPassword(currentPassword, password)
      if (!mounted.current) return
      setCurrentPassword('')
      setPassword('')
      setConfirmation('')
      setNotice('비밀번호가 변경되었습니다. 다음 로그인부터 새 비밀번호를 사용해 주세요.')
    } catch (cause) {
      if (mounted.current) setError(passwordErrorMessage(cause, 'change'))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <form
      className="space-y-4 border-t border-subtle pt-5"
      onSubmit={(event) => {
        event.preventDefault()
        void changePassword()
      }}
    >
      <div className="space-y-1">
        <h3 className="ui-setting-label">비밀번호 변경</h3>
        <p id="account-password-hint" className="ui-setting-hint">
          새 비밀번호는 8자 이상 입력해 주세요. 특수문자는 필수가 아닙니다.
        </p>
      </div>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <label className="block text-xs font-medium text-ink" htmlFor="account-current-password">
          현재 비밀번호
          <input
            id="account-current-password"
            type="password"
            name="currentPassword"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(event) => {
              setCurrentPassword(event.target.value)
              setNotice('')
            }}
            aria-describedby={error ? 'account-password-error' : undefined}
            className="ui-input mt-2"
          />
        </label>
        <label className="block text-xs font-medium text-ink" htmlFor="account-new-password">
          새 비밀번호
          <input
            id="account-new-password"
            type="password"
            name="newPassword"
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={256}
            value={password}
            onChange={(event) => {
              setPassword(event.target.value)
              setNotice('')
            }}
            aria-describedby={`account-password-hint${error ? ' account-password-error' : ''}`}
            className="ui-input mt-2"
          />
        </label>
        <label className="block text-xs font-medium text-ink" htmlFor="account-confirm-password">
          새 비밀번호 확인
          <input
            id="account-confirm-password"
            type="password"
            name="confirmPassword"
            autoComplete="new-password"
            required
            maxLength={256}
            value={confirmation}
            onChange={(event) => {
              setConfirmation(event.target.value)
              setNotice('')
            }}
            aria-describedby={error ? 'account-password-error' : undefined}
            className="ui-input mt-2"
          />
        </label>
        {error && (
          <p
            id="account-password-error"
            role="alert"
            className="text-xs leading-relaxed text-red-600 dark:text-red-400"
          >
            {error}
          </p>
        )}
        {notice && (
          <p
            role="status"
            className="text-xs leading-relaxed text-emerald-700 dark:text-emerald-400"
          >
            {notice}
          </p>
        )}
        <button type="submit" className="ui-button ui-button-primary min-h-11 w-full">
          {busy && <Icon name="loader" className="h-4 w-4 animate-spin" />}
          {busy ? '변경 중…' : '비밀번호 변경'}
        </button>
      </fieldset>
    </form>
  )
}

export default function AccountSettings() {
  const [account, setAccount] = useState<{ email: string; verified: boolean } | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [code, setCode] = useState('')
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

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    getAccountEmail()
      .then((value) => {
        if (active) setAccount(value)
      })
      .catch((cause: unknown) => {
        if (active) setError(passwordErrorMessage(cause, 'verify'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [refresh])

  async function verifyEmail(action: 'send' | 'confirm') {
    if (pending.current || loading) return
    pending.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (action === 'send') {
        await sendAccountEmailVerificationCode()
        if (!mounted.current) return
        setSent(true)
        setCode('')
        setNotice('인증 코드를 보냈습니다. 메일함을 확인해 주세요.')
      } else {
        await verifyAccountEmailCode(code)
        if (!mounted.current) return
        setAccount((value) => value && { ...value, verified: true })
        setCode('')
        setNotice(
          '이메일 인증이 완료되었습니다. 비밀번호를 잊어도 이 이메일로 재설정할 수 있습니다.'
        )
      }
    } catch (cause) {
      if (mounted.current) setError(passwordErrorMessage(cause, 'verify'))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <section className="space-y-3" aria-labelledby="account-email-title">
        <h3 id="account-email-title" className="ui-setting-label">
          계정 이메일
        </h3>
        {loading && (
          <p role="status" className="ui-setting-hint">
            계정 정보를 확인하고 있습니다…
          </p>
        )}
        {account && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <p className="break-all text-sm text-ink">{account.email}</p>
              <span
                className={`text-xs ${account.verified ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted'}`}
              >
                {account.verified ? '인증 완료' : '인증 필요'}
              </span>
            </div>
            <p className="ui-setting-hint">
              {account.verified
                ? '비밀번호를 잊었을 때 이 이메일로 인증 코드를 받습니다.'
                : '이메일을 인증해 두면 비밀번호를 잊었을 때 직접 재설정할 수 있습니다.'}
            </p>
            {!account.verified && (
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  void verifyEmail('confirm')
                }}
              >
                <fieldset disabled={busy || loading} className="min-w-0 space-y-3">
                  {sent && (
                    <>
                      <label
                        className="block text-xs font-medium text-ink"
                        htmlFor="account-email-code"
                      >
                        이메일 인증 코드
                        <input
                          id="account-email-code"
                          name="emailCode"
                          required
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          pattern="[0-9]{6}"
                          maxLength={6}
                          value={code}
                          onChange={(event) =>
                            setCode(event.target.value.replace(/\D/g, '').slice(0, 6))
                          }
                          aria-describedby={error ? 'account-email-error' : undefined}
                          className="ui-input mt-2"
                        />
                      </label>
                      <button type="submit" className="ui-button ui-button-primary min-h-11">
                        {busy ? '처리 중…' : '이메일 인증'}
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() => void verifyEmail('send')}
                    className="ui-button ui-button-secondary min-h-11"
                  >
                    {sent ? '코드 다시 받기' : busy ? '발송 중…' : '인증 코드 받기'}
                  </button>
                </fieldset>
              </form>
            )}
          </>
        )}
        {error && (
          <p
            id="account-email-error"
            role="alert"
            className="text-xs leading-relaxed text-red-600 dark:text-red-400"
          >
            {error}
          </p>
        )}
        {!account && !loading && (
          <button
            type="button"
            onClick={() => setRefresh((value) => value + 1)}
            className="ui-button ui-button-secondary"
          >
            계정 다시 확인
          </button>
        )}
        {notice && (
          <p role="status" className="ui-setting-hint">
            {notice}
          </p>
        )}
      </section>
      <ChangePasswordForm />
    </div>
  )
}
