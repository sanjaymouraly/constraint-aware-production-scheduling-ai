import { beforeEach, describe, expect, it } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { errorMessage, tokenStore } from '@/api/client'

describe('tokenStore', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('stores, reads and clears the access token', () => {
    expect(tokenStore.get()).toBeNull()
    tokenStore.set('abc123')
    expect(tokenStore.get()).toBe('abc123')
    expect(window.localStorage.getItem('scheduling.token')).toBe('abc123')
    tokenStore.clear()
    expect(tokenStore.get()).toBeNull()
  })

  it('survives a storage failure instead of throwing', () => {
    const original = window.localStorage.setItem
    window.localStorage.setItem = () => {
      throw new Error('storage disabled')
    }
    expect(() => tokenStore.set('x')).not.toThrow()
    window.localStorage.setItem = original
  })
})

describe('errorMessage', () => {
  it('extracts a string detail from an API error', () => {
    const error = new AxiosError('failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      data: { detail: 'Machine is not eligible for operation 3' },
      status: 400,
      statusText: 'Bad Request',
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    })
    expect(errorMessage(error)).toBe('Machine is not eligible for operation 3')
  })

  it('joins validation details into a single readable message', () => {
    const error = new AxiosError('failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      data: {
        detail: [
          { loc: ['body', 'horizon_start'], msg: 'field required' },
          { loc: ['body', 'engine'], msg: 'invalid value' },
        ],
      },
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    })
    expect(errorMessage(error)).toBe('horizon_start: field required; engine: invalid value')
  })

  it('explains that the API server is unreachable', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK')
    expect(errorMessage(error)).toMatch(/Cannot reach the API server/)
  })

  it('falls back to the error message and a generic default', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom')
    expect(errorMessage('weird')).toBe('Unexpected error')
  })
})
