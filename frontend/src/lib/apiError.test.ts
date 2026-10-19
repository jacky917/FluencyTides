import { describe, expect, it } from 'vitest'
import { describeApiError, failureTitle, isBackendError, readErrorBody, retryTransient } from './apiError'

describe('describeApiError', () => {
  it('keeps the backend message verbatim with its status', () => {
    const error = { error_code: 'INVALID_LLM_ROUTE', message: "模型 'x' 不在端點 'yinli' 的白名單", status: 422 }
    expect(describeApiError(error)).toEqual({ status: 422, message: "模型 'x' 不在端點 'yinli' 的白名單" })
    expect(isBackendError(error)).toBe(true)
  })

  it('joins FastAPI validation errors by field', () => {
    const error = {
      message: 'Request failed with status code 422',
      response: {
        status: 422,
        data: { detail: [{ loc: ['body', 'endpoint'], msg: 'too short' }, { loc: ['body', 'model'], msg: 'missing' }] },
      },
    }
    expect(describeApiError(error)).toEqual({ status: 422, message: 'endpoint: too short；model: missing' })
    expect(isBackendError(error)).toBe(false)
  })

  it('falls back when the validation detail is empty', () => {
    const error = { message: 'Request failed with status code 422', response: { status: 422, data: { detail: [] } } }
    expect(describeApiError(error).message).toContain('HTTP 422')
  })

  it('describes a proxy error without a backend body', () => {
    const error = { message: 'Request failed with status code 504', response: { status: 504, data: '<html>' } }
    expect(describeApiError(error)).toEqual({
      status: 504,
      message: '後端或代理回應 HTTP 504：Request failed with status code 504',
    })
  })

  it('reports a missing response as unreachable, without a status', () => {
    expect(describeApiError({ message: 'Network Error' })).toEqual({
      message: '連不到後端（網路或代理中斷）：Network Error',
    })
    expect(describeApiError(null).message).toBe('null')
  })
})

describe('failureTitle', () => {
  it('only claims nothing was written for a 4xx', () => {
    expect(failureTitle(404, '儲存')).toBe('無法儲存')
    expect(failureTitle(422, '儲存')).toBe('無法儲存')
  })

  it.each([503, 500, 502, 504, undefined])('treats %s as an unknown outcome', (status) => {
    expect(failureTitle(status, '恢復預設')).toBe('恢復預設未完成或結果未知，請重新整理查看')
  })
})

describe('retryTransient', () => {
  it('retries once, only without a response or on a 5xx', () => {
    expect(retryTransient(0, { message: 'Network Error' })).toBe(true)
    expect(retryTransient(0, { error_code: 'X', message: 'm', status: 502 })).toBe(true)
    expect(retryTransient(1, { error_code: 'X', message: 'm', status: 502 })).toBe(false)
    expect(retryTransient(0, { error_code: 'CARD_NOT_FOUND', message: 'm', status: 404 })).toBe(false)
    expect(retryTransient(0, { error_code: 'INVALID_ANKI_SEARCH', message: 'm', status: 400 })).toBe(false)
  })
})

describe('readErrorBody', () => {
  it('parses a JSON error delivered as a Blob', async () => {
    const blob = new Blob([JSON.stringify({ error_code: 'MEDIA_FILE_NOT_FOUND', message: '沒有 x.mp3' })], {
      type: 'application/json',
    })
    expect(await readErrorBody(blob)).toEqual({ error_code: 'MEDIA_FILE_NOT_FOUND', message: '沒有 x.mp3' })
  })

  it('keeps non-JSON bodies as they are', async () => {
    const html = new Blob(['<html>502</html>'], { type: 'text/html' })
    const broken = new Blob(['{not json'], { type: 'application/json' })
    expect(await readErrorBody(html)).toBe(html)
    expect(await readErrorBody(broken)).toBe(broken)
    expect(await readErrorBody({ detail: 'x' })).toEqual({ detail: 'x' })
  })
})
