'use client'

import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { CognitoUser } from 'amazon-cognito-identity-js'
import { refreshSession, signOut as cognitoSignOut } from '@/lib/cognito'

interface AuthContextValue {
  user: CognitoUser | null
  isLoading: boolean
  setUser: (user: CognitoUser | null) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CognitoUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // If Cognito is not configured, skip auth entirely
    if (!process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID) {
      setIsLoading(false)
      return
    }
    refreshSession()
      .then(setUser)
      .finally(() => setIsLoading(false))
  }, [])

  function logout() {
    if (user) cognitoSignOut(user)
    // 화면에 남은 미팅·연결·캐시는 전 사용자 것이다 — 다음 사용자가 보지 않도록 새로 불러온다
    window.location.reload()
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
