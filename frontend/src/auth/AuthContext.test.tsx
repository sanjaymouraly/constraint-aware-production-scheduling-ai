import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthContext'
import { useAuth } from '@/auth/useAuth'
import { LoginPage } from '@/pages/LoginPage'
import { api, tokenStore } from '@/api/client'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const mockedApi = vi.mocked(api, true)

function Probe() {
  const { user, canPlan, isAdmin, hasRole, logout } = useAuth()
  return (
    <div>
      <p data-testid="user">{user?.username ?? 'anonymous'}</p>
      <p data-testid="role">{user?.role ?? 'none'}</p>
      <p data-testid="can-plan">{String(canPlan)}</p>
      <p data-testid="is-admin">{String(isAdmin)}</p>
      <p data-testid="has-role-planner">{String(hasRole('planner'))}</p>
      <button type="button" onClick={logout}>
        logout
      </button>
    </div>
  )
}

describe('AuthProvider', () => {
  beforeEach(() => {
    tokenStore.clear()
    mockedApi.get.mockReset()
    mockedApi.post.mockReset()
  })

  it('starts anonymous when no token is stored', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('anonymous'))
    expect(screen.getByTestId('can-plan')).toHaveTextContent('false')
    expect(mockedApi.get).not.toHaveBeenCalled()
  })

  it('restores the session from a stored token', async () => {
    tokenStore.set('stored-token')
    mockedApi.get.mockResolvedValue({
      data: { id: 1, username: 'admin', role: 'admin', email: 'admin@example.com' },
    })
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('admin'))
    expect(screen.getByTestId('is-admin')).toHaveTextContent('true')
    expect(screen.getByTestId('can-plan')).toHaveTextContent('true')
    expect(mockedApi.get).toHaveBeenCalledWith('/auth/me')
  })

  it('clears the token when the stored session is no longer valid', async () => {
    tokenStore.set('stale-token')
    mockedApi.get.mockRejectedValue(new Error('401'))
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('anonymous'))
    expect(tokenStore.get()).toBeNull()
  })

  it('logs the user out', async () => {
    tokenStore.set('token')
    mockedApi.get.mockResolvedValue({
      data: { id: 1, username: 'planner', role: 'planner', email: 'p@example.com' },
    })
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('planner'))
    expect(screen.getByTestId('can-plan')).toHaveTextContent('true')
    expect(screen.getByTestId('has-role-planner')).toHaveTextContent('true')
    await userEvent.click(screen.getByRole('button', { name: 'logout' }))
    expect(screen.getByTestId('user')).toHaveTextContent('anonymous')
    expect(tokenStore.get()).toBeNull()
  })
})

describe('LoginPage', () => {
  beforeEach(() => {
    tokenStore.clear()
    mockedApi.get.mockReset()
    mockedApi.post.mockReset()
  })

  it('signs in and stores the access token', async () => {
    mockedApi.post.mockResolvedValue({
      data: {
        access_token: 'jwt-token',
        token_type: 'bearer',
        user: { id: 1, username: 'admin', role: 'admin', email: 'admin@example.com' },
      },
    })
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route
            path="/login"
            element={
              <AuthProvider>
                <LoginPage />
              </AuthProvider>
            }
          />
          <Route
            path="/"
            element={
              <AuthProvider>
                <div>dashboard</div>
              </AuthProvider>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => expect(tokenStore.get()).toBe('jwt-token'))
    expect(mockedApi.post).toHaveBeenCalledWith('/auth/login', {
      username: 'admin',
      password: 'admin',
    })
    await waitFor(() => expect(screen.getByText('dashboard')).toBeInTheDocument())
  })

  it('shows the API error message when the credentials are rejected', async () => {
    mockedApi.post.mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 401, data: { detail: 'Invalid username or password' } },
      }),
    )
    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Invalid username or password'))
    expect(tokenStore.get()).toBeNull()
  })
})
