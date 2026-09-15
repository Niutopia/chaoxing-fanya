import apiClient, { apiRequest } from './client'

function requestConfig(options) {
  const signal = options && typeof options === 'object' ? options.signal : undefined
  return signal ? { signal } : {}
}

export function getAnswerConnection(options = {}) {
  return apiRequest(apiClient.get('/settings/answer-connection', requestConfig(options)))
}

export function updateAnswerConnection(payload, options = {}) {
  return apiRequest(apiClient.put('/settings/answer-connection', payload, requestConfig(options)))
}

export const saveAnswerConnection = updateAnswerConnection

export function deleteAnswerKey(options = {}) {
  return apiRequest(apiClient.delete('/settings/answer-connection/key', requestConfig(options)))
}

export const clearAnswerKey = deleteAnswerKey

export function testAnswerConnection(payload = {}, options = {}) {
  return apiRequest(apiClient.post('/settings/answer-connection/test', payload, requestConfig(options)))
}

export function getRuntimeSettings(options = {}) {
  return apiRequest(apiClient.get('/settings/runtime', requestConfig(options)))
}

export function updateRuntimeSettings(payload, options = {}) {
  return apiRequest(apiClient.put('/settings/runtime', payload, requestConfig(options)))
}

export const saveRuntimeSettings = updateRuntimeSettings
export const fetchAnswerConnection = getAnswerConnection
export const fetchRuntimeSettings = getRuntimeSettings

export default {
  getAnswerConnection,
  updateAnswerConnection,
  saveAnswerConnection,
  deleteAnswerKey,
  clearAnswerKey,
  testAnswerConnection,
  getRuntimeSettings,
  updateRuntimeSettings,
  saveRuntimeSettings,
}

export function getHealth() {
  return apiRequest(apiClient.get('/health'))
}
