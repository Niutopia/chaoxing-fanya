import apiClient, { apiRequest } from './client'

const accountPath = (accountId) => `/accounts/${encodeURIComponent(accountId)}`

function requestConfig(options) {
  const signal = options && typeof options === 'object' ? options.signal : undefined
  return signal ? { signal } : {}
}

export function listAccounts(options = {}) {
  return apiRequest(apiClient.get('/accounts', requestConfig(options)))
}

export function createAccount(payload, options = {}) {
  return apiRequest(apiClient.post('/accounts', payload, requestConfig(options)))
}

export function getAccount(accountId, options = {}) {
  return apiRequest(apiClient.get(accountPath(accountId), requestConfig(options)))
}

export function updateAccount(accountId, payload, options = {}) {
  return apiRequest(apiClient.patch(accountPath(accountId), payload, requestConfig(options)))
}

export function setAccountEnabled(accountId, enabled, options = {}) {
  return updateAccount(accountId, { enabled: Boolean(enabled) }, options)
}

export function patchAccount(accountId, payload, options = {}) {
  return updateAccount(accountId, payload, options)
}

export function deleteAccount(accountId, options = {}) {
  return apiRequest(apiClient.delete(accountPath(accountId), requestConfig(options)))
}

export function verifyAccount(accountId, options = {}) {
  return apiRequest(apiClient.post(`${accountPath(accountId)}/verify`, undefined, requestConfig(options)))
}

function refreshValue(options) {
  if (typeof options === 'boolean') return options
  return Boolean(options?.refresh)
}

export function getCourses(accountId, options = {}) {
  return apiRequest(
    apiClient.get(`${accountPath(accountId)}/courses`, {
      params: { refresh: refreshValue(options) ? 1 : 0 },
      ...requestConfig(options),
    }),
  )
}

export const listCourses = getCourses
export const getAccountCourses = getCourses

export function getPreferences(accountId, options = {}) {
  return apiRequest(apiClient.get(`${accountPath(accountId)}/preferences`, requestConfig(options)))
}

export function updatePreferences(accountId, payload, options = {}) {
  return apiRequest(apiClient.put(`${accountPath(accountId)}/preferences`, payload, requestConfig(options)))
}

export const savePreferences = updatePreferences

export const fetchAccounts = listAccounts
export const fetchAccount = getAccount
export const fetchCourses = getCourses
export const fetchPreferences = getPreferences
export const saveAccount = createAccount

export default {
  listAccounts,
  createAccount,
  saveAccount,
  getAccount,
  updateAccount,
  setAccountEnabled,
  patchAccount,
  deleteAccount,
  verifyAccount,
  getCourses,
  listCourses,
  getPreferences,
  updatePreferences,
  savePreferences,
}
