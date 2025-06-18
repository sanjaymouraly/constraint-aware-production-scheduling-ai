import { useContext } from 'react'
import { AuthContext } from '@/auth/context'
import type { AuthState } from '@/auth/context'

/** Access the current session.  Must be used inside an `<AuthProvider>`. */
export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
