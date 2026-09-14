import axios from 'axios'

/**
 * The one HTTP client used by the new application surface.
 *
 * API routes return a small `{ status, data }` envelope. `apiRequest` unwraps
 * successful responses so feature clients can work with domain values while
 * keeping transport and error handling in this module.
 */
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export class ApiError extends Error {
  constructor(message = '请求失败，请稍后重试', statusOrOptions = {}, code = null, details = null) {
    super(message)
    this.name = 'ApiError'
    const options =
      statusOrOptions && typeof statusOrOptions === 'object'
        ? statusOrOptions
        : { status: statusOrOptions, code, details }
    this.status = options.status ?? null
    this.code = options.code ?? null
    this.cause = options.cause
    this.details = options.details
  }
}

const fallbackMessage = '请求失败，请稍后重试'

function userFacingMessage(...candidates) {
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate
  }
  return fallbackMessage
}

function responsePayload(error) {
  const response = error?.response
  const payload = response?.data
  return payload && typeof payload === 'object' ? payload : {}
}

/** Convert an Axios/network error into the stable browser-facing error type. */
export function toApiError(error) {
  if (error instanceof ApiError) return error

  const payload = responsePayload(error)
  const status = error?.response?.status ?? error?.status ?? null
  const code = payload.code ?? error?.code ?? null
  const message = userFacingMessage(payload.msg, payload.message, error?.message)

  return new ApiError(message, {
    status,
    code,
    cause: error,
    details: payload,
  })
}

/**
 * Resolve an Axios request and unwrap the backend response envelope.
 * A thunk is accepted as a small convenience for callers that need to defer
 * constructing a request; endpoint clients normally pass an Axios promise.
 */
export async function apiRequest(promise) {
  try {
    const response = await (typeof promise === 'function' ? promise() : promise)
    const directEnvelope = typeof response?.status === 'boolean'
    const payload = directEnvelope
      ? response
      : response && Object.prototype.hasOwnProperty.call(response, 'data')
        ? response.data
        : response

    if (payload && typeof payload === 'object' && payload.status === false) {
      throw new ApiError(userFacingMessage(payload.msg, payload.message), {
        status: directEnvelope ? null : response?.status ?? null,
        code: payload.code ?? null,
        details: payload,
      })
    }

    if (
      payload &&
      typeof payload === 'object' &&
      payload.status === true &&
      Object.prototype.hasOwnProperty.call(payload, 'data')
    ) {
      return payload.data
    }

    return payload
  } catch (error) {
    throw toApiError(error)
  }
}

export default apiClient
