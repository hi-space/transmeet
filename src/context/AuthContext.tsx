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
    setUser(null)
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
