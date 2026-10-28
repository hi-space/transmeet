'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type EmailStatus } from '@/lib/api'
import {
  getAccountEmail,
  sendAccountEmailVerificationCode,
  verifyAccountEmailCode,
} from '@/lib/cognito'

type Info =
  | { step: 'account'; email: string }
  | { step: 'delivery'; email: string; status: EmailStatus['status'] }
type Action = 'check' | 'start' | 'resend' | 'confirm'

function errorMessage(error: unknown): string {
  const code =
    (error as { code?: string; name?: string })?.code ?? (error as { name?: string })?.name
  if (code === 'CodeMismatchException') return '인증 코드가 일치하지 않습니다. 다시 확인해 주세요.'
  if (code === 'ExpiredCodeException') return '인증 코드가 만료되었습니다. 코드를 다시 받아 주세요.'
  if (code === 'LimitExceededException' || code === 'TooManyRequestsException') {
    return '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.'
  }
  if (code === 'NotAuthorizedException') return '로그인이 만료되었습니다. 다시 로그인해 주세요.'
  if (error instanceof Error && /[가-힣]/.test(error.message)) return error.message
  return '메일 인증을 진행하지 못했습니다. 잠시 후 다시 시도해 주세요.'
}

export function useEmailVerification(enabled: boolean) {
  const [info, setInfo] = useState<Info | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const enabledRef = useRef(enabled)
  const mountedRef = useRef(false)
  const requestRef = useRef(0)
  const busyRef = useRef(false)
  enabledRef.current = enabled

  const run = useCallback(async (action: Action, code?: string) => {
    if ((action === 'confirm' || action === 'resend') && busyRef.current) return
    const request = ++requestRef.current
    const current = () => mountedRef.current && request === requestRef.current
    busyRef.current = true
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      if (!process.env.NEXT_PUBLIC_API_ENDPOINT) {
        throw new Error('서버에 연결한 뒤 메일 인증을 진행할 수 있습니다.')
      }
      if (action === 'confirm') {
        await verifyAccountEmailCode(code ?? '')
        if (!current()) return
      }
      const account = await getAccountEmail()
      if (!current()) return
      if (!account.verified) {
        setInfo({ step: 'account', email: account.email })
        if ((action === 'start' || action === 'resend') && enabledRef.current) {
          await sendAccountEmailVerificationCode()
          if (current()) setNotice('인증 코드를 보냈습니다. 메일함을 확인해 주세요.')
        }
        return
      }

      const delivery = await api.email.status()
      if (!current()) return
      setInfo({ step: 'delivery', ...delivery })
      const shouldSend =
        delivery.status !== 'verified' &&
        (action === 'resend' || (action !== 'check' && delivery.status === 'none'))
      if (shouldSend && enabledRef.current) {
        const sent = await api.email.verify()
        if (!current()) return
        setInfo({ step: 'delivery', ...sent })
        if (sent.status !== 'verified') {
          setNotice('수신 인증 메일을 보냈습니다. 메일의 링크를 눌러 주세요.')
        }
      }
    } catch (cause) {
      if (current()) setError(errorMessage(cause))
    } finally {
      if (current()) {
        busyRef.current = false
        setBusy(false)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    if (process.env.NEXT_PUBLIC_API_ENDPOINT) void run('check')
    return () => {
      mountedRef.current = false
    }
  }, [run])

  const pending = info?.step === 'delivery' && info.status === 'pending'
  useEffect(() => {
    if (!enabled || !pending) return
    let disposed = false
    let checking = false
    const check = async () => {
      if (document.hidden || busyRef.current || checking) return
      checking = true
      const request = requestRef.current
      try {
        const delivery = await api.email.status()
        if (!disposed && enabledRef.current && request === requestRef.current) {
          setInfo({ step: 'delivery', ...delivery })
          setError(null)
          if (delivery.status === 'verified') setNotice(null)
        }
      } catch {
        if (!disposed && enabledRef.current && request === requestRef.current) {
          setError('인증 상태를 확인하지 못했습니다. 다시 확인해 주세요.')
        }
      } finally {
        checking = false
      }
    }
    const interval = window.setInterval(() => void check(), 5000)
    const onFocus = () => void check()
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      disposed = true
      window.clearInterval(interval)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [enabled, pending])

  const start = () => {
    enabledRef.current = true
    void run('start')
  }
  const cancel = () => {
    enabledRef.current = false
    requestRef.current++
    busyRef.current = false
    setBusy(false)
    setError(null)
    setNotice(null)
  }

  return {
    info,
    busy,
    error,
    notice,
    start,
    cancel,
    check: () => void run('check'),
    resend: () => void run('resend'),
    confirm: (code: string) => void run('confirm', code),
  }
}
