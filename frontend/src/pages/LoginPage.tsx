import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Factory, Loader2 } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { errorMessage } from '@/api/client'

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-steel-100 via-steel-50 to-accent-50 p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-accent-600 text-white shadow-lift">
            <Factory size={24} />
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-steel-900">
            Production Scheduling AI
          </h1>
          <p className="mt-1 text-sm text-steel-500">
            Constraint-aware planning with OR-Tools CP-SAT
          </p>
        </div>

        <form onSubmit={submit} className="card space-y-4 p-6">
          <div>
            <label className="label" htmlFor="username">
              Username
            </label>
            <input
              id="username"
              className="input"
              value={username}
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="input"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && (
            <p role="alert" className="rounded-lg bg-danger-500/10 px-3 py-2 text-sm text-danger-700">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy && <Loader2 size={16} className="animate-spin" />}
            Sign in
          </button>

          <p className="text-center text-xs text-steel-400">
            Default local account: <span className="font-mono">admin / admin</span>
          </p>
        </form>
      </div>
    </div>
  )
}
