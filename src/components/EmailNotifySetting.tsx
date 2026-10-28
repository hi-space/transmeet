'use client'

import { useEffect, useState } from 'react'
import type { Settings } from '@/hooks/useSettings'
import { api, type EmailStatus } from '@/lib/api'
import Switch from '@/components/ui/Switch'

interface Props {
  settings: Settings
  onUpdate: (patch: Partial<Settings>) => void
}

const HAS_API = !!process.env.NEXT_PUBLIC_API_ENDPOINT

const STATUS_TEXT: Record<EmailStatus['status'], string> = {
  verified: '인증됨',
  pending: '인증 대기 중 — 메일함의 AWS 확인 메일에서 링크를 눌러 주세요',
  none: '인증 전',
}

/**
 * "미팅 종료 시 메일로 받기" 토글. SES 샌드박스라 받는 주소(로그인 이메일)를 먼저 인증해야 한다.
 * 토글을 켜면 인증 메일을 보낸다. 받는 주소는 서버가 로그인 토큰에서 읽는다.
 */
export default function EmailNotifySetting({ settings, onUpdate }: Props) {
  const [info, setInfo] = useState<EmailStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!HAS_API) return
    api.email
      .status()
      .then(setInfo)
      .catch(() => setError('메일 인증 상태를 불러오지 못했습니다'))
  }, [])

  const requestVerification = async () => {
    setBusy(true)
    setError(null)
    try {
      setInfo(await api.email.verify())
    } catch {
      setError('인증 메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요')
    } finally {
      setBusy(false)
    }
  }

  const toggle = () => {
    const next = !settings.emailOnEnd
    onUpdate({ emailOnEnd: next })
    if (next && HAS_API && info?.status === 'none') void requestVerification()
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-3">
        <span className="ui-setting-label">회의 종료 시 메일로 받기</span>
        <Switch label="회의 종료 시 메일로 받기" checked={settings.emailOnEnd} onChange={toggle} />
      </div>
      {settings.emailOnEnd && (
        <div className="space-y-1">
          {info && (
            <p className="break-all text-xs leading-relaxed text-muted">
              {info.email} ·{' '}
              <span
                className={
                  info.status === 'verified'
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-amber-600 dark:text-amber-400'
                }
              >
                {STATUS_TEXT[info.status]}
              </span>
            </p>
          )}
          {info && info.status !== 'verified' && (
            <button
              onClick={requestVerification}
              disabled={busy}
              className="ui-button ui-button-tinted"
            >
              {busy
                ? '보내는 중…'
                : info.status === 'pending'
                  ? '인증 메일 다시 보내기'
                  : '인증 메일 보내기'}
            </button>
          )}
          {error && (
            <p role="alert" className="text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
          <p className="ui-setting-hint">요약과 녹음 링크를 로그인 이메일로 보냅니다.</p>
        </div>
      )}
    </div>
  )
}
