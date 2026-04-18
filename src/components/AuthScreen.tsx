'use client'

import { useState } from 'react'
import type { CognitoUser } from 'amazon-cognito-identity-js'
import { signIn, completeNewPassword } from '@/lib/cognito'

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
    if (!pendingUser) return
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

  const inputClass =
    'w-full px-3 py-2.5 text-sm rounded-lg bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-800 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500'

  const btnPrimary =
    'w-full py-2.5 rounded-lg text-sm font-semibold bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed'

  return (
    <div className="flex items-center justify-center h-screen bg-white dark:bg-slate-950">
      {/* Background orbs */}
      <div className="absolute top-[8%] left-[3%] w-72 h-72 rounded-full bg-emerald-300/10 dark:bg-emerald-600/8 blur-3xl pointer-events-none" />
      <div className="absolute top-[35%] right-[5%] w-96 h-96 rounded-full bg-slate-300/5 dark:bg-slate-600/5 blur-3xl pointer-events-none" />

      <div className="relative w-full max-w-sm mx-4">
        {/* Logo */}
        <div className="flex items-center justify-center gap-2.5 mb-8">
          <div className="w-9 h-9 rounded-xl bg-emerald-600 flex items-center justify-center shadow-lg">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-5 h-5"
            >
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" x2="12" y1="19" y2="22" />
            </svg>
          </div>
          <span className="text-xl font-bold text-slate-900 dark:text-white">TransMeet</span>
        </div>

        {/* Card */}
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-xl shadow-slate-500/5">
          {pendingUser ? (
            <>
              <h2 className="text-base font-semibold text-slate-700 dark:text-slate-200 mb-1 text-center">
                새 비밀번호 설정
              </h2>
              <p className="text-xs text-slate-400 dark:text-slate-500 text-center mb-5">
                보안을 위해 새 비밀번호를 설정해 주세요.
              </p>
              <div className="space-y-3">
                <input
                  type="password"
                  placeholder="새 비밀번호"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className={inputClass}
                  autoComplete="new-password"
                />
                <input
                  type="password"
                  placeholder="새 비밀번호 확인"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleNewPassword()}
                  className={inputClass}
                  autoComplete="new-password"
                />
                {error && <p className="text-xs text-red-500">{error}</p>}
                <button onClick={handleNewPassword} disabled={loading} className={btnPrimary}>
                  {loading ? '설정 중...' : '비밀번호 설정'}
                </button>
              </div>
            </>
          ) : (
            <>
              <h2 className="text-base font-semibold text-slate-700 dark:text-slate-200 mb-5 text-center">
                로그인
              </h2>
              <div className="space-y-3">
                <input
                  type="email"
                  placeholder="이메일"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputClass}
                  autoComplete="email"
                />
                <input
                  type="password"
                  placeholder="비밀번호"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
                  className={inputClass}
                  autoComplete="current-password"
                />
                {error && <p className="text-xs text-red-500">{error}</p>}
                <button onClick={handleLogin} disabled={loading} className={btnPrimary}>
                  {loading ? '로그인 중...' : '로그인'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
