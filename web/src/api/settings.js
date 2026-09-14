import apiClient, { apiRequest } from './client'

export function getAnswerConnection() {
  return apiRequest(apiClient.get('/settings/answer-connection'))
}

export function updateAnswerConnection(payload) {
  return apiRequest(apiClient.put('/settings/answer-connection', payload))
}

export const saveAnswerConnection = updateAnswerConnection

export function deleteAnswerKey() {
  return apiRequest(apiClient.delete('/settings/answer-connection/key'))
}

export const clearAnswerKey = deleteAnswerKey

export function testAnswerConnection(payload = {}) {
  return apiRequest(apiClient.post('/settings/answer-connection/test', payload))
}

export function getRuntimeSettings() {
  return apiRequest(apiClient.get('/settings/runtime'))
}

export function updateRuntimeSettings(payload) {
  return apiRequest(apiClient.put('/settings/runtime', payload))
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
