import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, tokenStore } from '@/api/client'
import type { Role, TokenResponse, User } from '@/api/types'
import { AuthContext } from '@/auth/context'
import type { AuthState } from '@/auth/context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadUser = useCallback(async () => {
    if (!tokenStore.get()) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const { data } = await api.get<User>('/auth/me')
      setUser(data)
    } catch {
      tokenStore.clear()
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadUser()
  }, [loadUser])

  const login = useCallback(async (username: string, password: string) => {
    setError(null)
    const { data } = await api.post<TokenResponse>('/auth/login', { username, password })
    tokenStore.set(data.access_token)
    setUser(data.user)
  }, [])

  const logout = useCallback(() => {
    tokenStore.clear()
    setUser(null)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      error,
      login,
      logout,
      hasRole: (...roles: Role[]) => (user ? roles.includes(user.role) : false),
      canPlan: user ? user.role === 'admin' || user.role === 'planner' : false,
      isAdmin: user?.role === 'admin',
    }),
    [user, loading, error, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
