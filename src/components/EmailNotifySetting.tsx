'use client'

import { useId, useState } from 'react'
import type { Settings } from '@/hooks/useSettings'
import { useEmailVerification } from '@/hooks/useEmailVerification'
import Switch from '@/components/ui/Switch'

interface Props {
  settings: Settings
  onUpdate: (patch: Partial<Settings>) => void
}

/**
 * 옵션을 켜면 계정 이메일 확인 → 수신 주소 인증을 순서대로 안내한다.
 * 상태 조회와 설정 재열기는 메일을 보내지 않는다.
 */
export default function EmailNotifySetting({ settings, onUpdate }: Props) {
  const verification = useEmailVerification(settings.emailOnEnd)
  const { info, busy, error, notice } = verification
  const [code, setCode] = useState('')
  const id = useId()
  const codeId = `${id}-code`
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  const ready = info?.step === 'delivery' && info.status === 'verified'

  const toggle = (next: boolean) => {
    onUpdate({ emailOnEnd: next })
    if (next) verification.start()
    else verification.cancel()
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="ui-setting-label">회의 종료 시 메일로 받기</span>
        <Switch label="회의 종료 시 메일로 받기" checked={settings.emailOnEnd} onChange={toggle} />
      </div>
      <p className="ui-setting-hint">요약과 녹음 링크를 로그인 이메일로 보냅니다.</p>
      {settings.emailOnEnd && (
        <div className="space-y-3 rounded-lg border border-border p-3">
          {info && <p className="break-all text-xs text-muted">{info.email}</p>}
          <div role="status" aria-live="polite" className="space-y-1 text-xs leading-relaxed">
            <p className={ready ? 'text-emerald-700 dark:text-emerald-400' : 'text-ink'}>
              {ready
                ? '인증 완료 — 회의 종료 후 메일을 보냅니다.'
                : busy
                  ? '메일 인증을 처리하고 있습니다…'
                  : '인증 대기 — 아래 단계를 완료하면 메일을 받을 수 있습니다.'}
            </p>
            {notice && <p className="text-muted">{notice}</p>}
          </div>

          {info?.step === 'account' && (
            <form
              className="space-y-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (!busy && /^\d{6}$/.test(code)) verification.confirm(code)
              }}
            >
              <p className="text-xs font-medium text-ink">1. 계정 이메일 확인</p>
              <label htmlFor={codeId} className="block text-xs text-ink">
                이메일 인증 코드
              </label>
              <div className="flex gap-2">
                <input
                  id={codeId}
                  className="ui-input min-w-0 flex-1"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                  aria-describedby={`${hintId}${error ? ` ${errorId}` : ''}`}
                  aria-invalid={error ? true : undefined}
                  placeholder="6자리 코드"
                />
                <button
                  type="submit"
                  className="ui-button ui-button-primary min-h-11 shrink-0"
                  disabled={busy || !/^\d{6}$/.test(code)}
                >
                  코드 확인
                </button>
              </div>
              <p id={hintId} className="ui-setting-hint">
                코드 확인 후 회의 메일 수신용 인증 링크를 보내드립니다.
              </p>
              <button
                type="button"
                className="ui-button ui-button-tinted min-h-11"
                disabled={busy}
                onClick={verification.resend}
              >
                인증 코드 다시 보내기
              </button>
            </form>
          )}

          {info?.step === 'delivery' && !ready && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-ink">회의 메일 수신 인증</p>
              <p className="ui-setting-hint">
                {info.status === 'pending'
                  ? '메일함에서 AWS 수신 인증 메일의 링크를 눌러 주세요. 이 화면으로 돌아오면 완료 여부를 자동으로 확인합니다.'
                  : '로그인 이메일로 보낸 인증 링크를 한 번 확인하면 됩니다.'}
              </p>
              <div className="flex flex-wrap gap-2">
                {info.status === 'pending' && (
                  <button
                    type="button"
                    className="ui-button ui-button-tinted min-h-11"
                    disabled={busy}
                    onClick={verification.check}
                  >
                    인증 상태 확인
                  </button>
                )}
                <button
                  type="button"
                  className="ui-button ui-button-tinted min-h-11"
                  disabled={busy}
                  onClick={verification.resend}
                >
                  {info.status === 'pending' ? '인증 메일 다시 보내기' : '인증 메일 보내기'}
                </button>
              </div>
            </div>
          )}
          {!info && !busy && (
            <button
              type="button"
              className="ui-button ui-button-tinted min-h-11"
              onClick={verification.start}
            >
              인증 다시 시도
            </button>
          )}
          {error && (
            <p id={errorId} role="alert" className="text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
