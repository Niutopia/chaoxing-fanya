import apiClient, { apiRequest } from './client'

const accountPath = (accountId) => `/accounts/${encodeURIComponent(accountId)}`

export function listAccounts() {
  return apiRequest(apiClient.get('/accounts'))
}

export function createAccount(payload) {
  return apiRequest(apiClient.post('/accounts', payload))
}

export function getAccount(accountId) {
  return apiRequest(apiClient.get(accountPath(accountId)))
}

export function updateAccount(accountId, payload) {
  return apiRequest(apiClient.patch(accountPath(accountId), payload))
}

export function setAccountEnabled(accountId, enabled) {
  return updateAccount(accountId, { enabled: Boolean(enabled) })
}

export function patchAccount(accountId, payload) {
  return updateAccount(accountId, payload)
}

export function deleteAccount(accountId) {
  return apiRequest(apiClient.delete(accountPath(accountId)))
}

export function verifyAccount(accountId) {
  return apiRequest(apiClient.post(`${accountPath(accountId)}/verify`))
}

function refreshValue(options) {
  if (typeof options === 'boolean') return options
  return Boolean(options?.refresh)
}

export function getCourses(accountId, options = {}) {
  return apiRequest(
    apiClient.get(`${accountPath(accountId)}/courses`, {
      params: { refresh: refreshValue(options) ? 1 : 0 },
    }),
  )
}

export const listCourses = getCourses
export const getAccountCourses = getCourses

export function getPreferences(accountId) {
  return apiRequest(apiClient.get(`${accountPath(accountId)}/preferences`))
}

export function updatePreferences(accountId, payload) {
  return apiRequest(apiClient.put(`${accountPath(accountId)}/preferences`, payload))
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
