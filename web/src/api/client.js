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

const fallbackMessage = '请求失败，请稍后重试'
const redactionMarker = '[redacted]'
const maxDetailDepth = 6
const maxDetailEntries = 80
const maxDetailStringLength = 2000

const sensitiveKeyPattern = /(?:pass(?:word|wd)?|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|credential|private[-_]?key|client[-_]?secret|^key$)/i
const transportKeyPattern = /^(?:config|request|response|cause|stack|headers?|body|requestbody)$/i

function normalizeKey(key) {
  return String(key).replace(/[^a-z0-9]/gi, '').toLowerCase()
}

function isSensitiveKey(key) {
  return sensitiveKeyPattern.test(String(key))
}

function isTransportKey(key) {
  return transportKeyPattern.test(normalizeKey(key))
}

function addSecret(value, secrets) {
  if (typeof value !== 'string') return
  const candidate = value.trim()
  if (candidate.length >= 3 && candidate !== redactionMarker) secrets.add(candidate)
}

function collectSecretsFromText(value, secrets) {
  const text = String(value)
  let match
  const assignmentPattern = /(?:password|passwd|pass|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|key)\s*[:=]\s*(?:"([^"]+)"|'([^']+)'|([^,;&\s}]+))/gi
  while ((match = assignmentPattern.exec(text))) {
    addSecret(match[1] ?? match[2] ?? match[3], secrets)
  }
  const bearerPattern = /\bbearer\s+([^\s,;}]+)/gi
  while ((match = bearerPattern.exec(text))) addSecret(match[1], secrets)
}

function collectSensitiveValues(value, secrets = new Set(), seen = new WeakSet(), context = false) {
  if (value == null) return secrets

  if (typeof value === 'string') {
    if (context) {
      try {
        const parsed = JSON.parse(value)
        collectSensitiveValues(parsed, secrets, seen, true)
      } catch {
        collectSecretsFromText(value, secrets)
      }
    }
    return secrets
  }

  if (typeof value !== 'object') {
    if (context) addSecret(String(value), secrets)
    return secrets
  }

  if (seen.has(value)) return secrets
  seen.add(value)

  if (Array.isArray(value)) {
    for (const item of value) collectSensitiveValues(item, secrets, seen, context)
    return secrets
  }

  for (const [key, item] of Object.entries(value)) {
    const childContext = context || isSensitiveKey(key) || isTransportKey(key) || normalizeKey(key) === 'data'
    if (isSensitiveKey(key) && typeof item === 'string') addSecret(item, secrets)
    collectSensitiveValues(item, secrets, seen, childContext)
  }
  return secrets
}

function redactString(value, secrets) {
  if (typeof value !== 'string') return value
  let result = value
  for (const secret of [...secrets].sort((left, right) => right.length - left.length)) {
    result = result.split(secret).join(redactionMarker)
  }
  return result
    .replace(/\bbearer\s+[^\s,;}]+/gi, 'Bearer [redacted]')
    .replace(/((?:password|passwd|pass|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|key))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,;&\s}]+)/gi, '$1=[redacted]')
    .slice(0, maxDetailStringLength)
}

function sanitizeDetails(value, secrets, depth = 0, seen = new WeakSet()) {
  if (depth > maxDetailDepth) return redactionMarker
  if (value == null || typeof value === 'boolean') return value
  if (typeof value === 'number') return Number.isFinite(value) ? value : redactionMarker
  if (typeof value === 'string') return redactString(value, secrets)
  if (typeof value !== 'object') return undefined

  if (seen.has(value)) return redactionMarker
  seen.add(value)

  if (Array.isArray(value)) {
    return value
      .slice(0, maxDetailEntries)
      .map((item) => sanitizeDetails(item, secrets, depth + 1, seen))
  }

  const safe = {}
  for (const [key, item] of Object.entries(value).slice(0, maxDetailEntries)) {
    if (isTransportKey(key)) continue
    if (isSensitiveKey(key)) {
      safe[key] = redactionMarker
      continue
    }
    const sanitized = sanitizeDetails(item, secrets, depth + 1, seen)
    if (sanitized !== undefined) safe[key] = sanitized
  }
  return safe
}

function scalarStatus(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return null
}

function scalarCode(value) {
  if (typeof value !== 'string' || !value.trim()) return null
  return value.trim().slice(0, 128)
}

export class ApiError extends Error {
  constructor(message = fallbackMessage, statusOrOptions = {}, code = null, details = null) {
    const options =
      statusOrOptions && typeof statusOrOptions === 'object'
        ? statusOrOptions
        : { status: statusOrOptions, code, details }
    const secrets = options.sensitiveValues instanceof Set
      ? options.sensitiveValues
      : collectSensitiveValues(options.details)
    super(redactString(userFacingMessage(message), secrets))
    this.name = 'ApiError'
    this.status = scalarStatus(options.status)
    this.code = scalarCode(options.code)
    this.details = sanitizeDetails(options.details ?? null, secrets)
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      status: this.status,
      code: this.code,
      details: this.details,
    }
  }
}

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
  const sensitiveValues = collectSensitiveValues(error)
  const message = redactString(
    userFacingMessage(payload.msg, payload.message, error?.message),
    sensitiveValues,
  )

  return new ApiError(message, {
    status,
    code,
    details: sanitizeDetails(payload, sensitiveValues),
    sensitiveValues,
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
