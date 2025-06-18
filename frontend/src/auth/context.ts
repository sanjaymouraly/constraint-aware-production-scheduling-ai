import { createContext } from 'react'
import type { Role, User } from '@/api/types'

export interface AuthState {
  user: User | null
  loading: boolean
  error: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (...roles: Role[]) => boolean
  canPlan: boolean
  isAdmin: boolean
}

/**
 * The context object lives in its own module so that the provider component
 * and the `useAuth` hook can live in separate files.  Keeping them apart
 * satisfies the react-refresh lint rule (a module that exports a component
 * should not also export plain functions).
 */
export const AuthContext = createContext<AuthState | undefined>(undefined)

export const AuthContextProvider = AuthContext.Provider
