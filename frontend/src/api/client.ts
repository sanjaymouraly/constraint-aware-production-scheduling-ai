import axios, { AxiosError, type AxiosInstance } from 'axios'

const TOKEN_KEY = 'scheduling.token'

export const tokenStore = {
  get(): string | null {
    try {
      return window.localStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string) {
    try {
      window.localStorage.setItem(TOKEN_KEY, token)
    } catch {
      /* storage unavailable (private mode) — the session stays in memory */
    }
  },
  clear() {
    try {
      window.localStorage.removeItem(TOKEN_KEY)
    } catch {
      /* ignore */
    }
  },
}

export const api: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 300000,
})

api.interceptors.request.use((config) => {
  const token = tokenStore.get()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      tokenStore.clear()
      if (!window.location.pathname.startsWith('/login')) {
        window.location.assign('/login')
      }
    }
    return Promise.reject(error)
  },
)

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item: any) => `${(item.loc || []).slice(1).join('.')}: ${item.msg}`)
        .join('; ')
    }
    if (error.code === 'ERR_NETWORK') {
      return 'Cannot reach the API server. Is the backend running on port 8000?'
    }
    return error.message
  }
  if (error instanceof Error) return error.message
  return 'Unexpected error'
}
