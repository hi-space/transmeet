'use client'

import { useEffect, useState } from 'react'
import type { Settings } from '@/hooks/useSettings'
import { api, type EmailStatus } from '@/lib/api'

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
    <div className="space-y-2">
      <p className="text-[0.6875rem] font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">
        미팅 종료 메일
      </p>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-slate-600 dark:text-slate-300">종료 시 메일로 받기</span>
        <button
          onClick={toggle}
          aria-label="미팅 종료 시 메일로 받기 토글"
          aria-pressed={settings.emailOnEnd}
          className={`relative shrink-0 w-10 h-5 rounded-full transition-colors duration-200 focus:outline-none ${
            settings.emailOnEnd ? 'bg-primary' : 'bg-slate-200 dark:bg-slate-700'
          }`}
        >
          <span
            className="absolute top-[2px] w-4 h-4 bg-white rounded-full shadow-sm transition-all duration-200"
            style={{ left: settings.emailOnEnd ? '22px' : '2px' }}
          />
        </button>
      </div>
      {settings.emailOnEnd && (
        <div className="space-y-1">
          {info && (
            <p className="text-[0.6875rem] text-slate-500 dark:text-slate-400 break-all">
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
              className="text-[0.6875rem] text-accent-700 dark:text-accent-400 hover:underline disabled:opacity-50"
            >
              {busy
                ? '보내는 중…'
                : info.status === 'pending'
                  ? '인증 메일 다시 보내기'
                  : '인증 메일 보내기'}
            </button>
          )}
          {error && <p className="text-[0.6875rem] text-red-500">{error}</p>}
        </div>
      )}
      <p className="text-[0.625rem] text-slate-400 dark:text-slate-500">
        &quot;미팅 종료&quot;를 누르면 요약과 녹음 파일 다운로드 링크를 로그인 이메일로 보냅니다
      </p>
    </div>
  )
}
