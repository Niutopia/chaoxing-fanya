import apiClient, { apiRequest } from './client'

const taskPath = (taskId) => `/tasks/${encodeURIComponent(taskId)}`

function requestConfig(options) {
  const signal = options && typeof options === 'object' ? options.signal : undefined
  return signal ? { signal } : {}
}

export function listTasks(options = {}) {
  return apiRequest(apiClient.get('/tasks', requestConfig(options)))
}

export function startTask(accountId, payload = {}, options = {}) {
  const body = Array.isArray(payload) ? { course_ids: payload } : payload
  return apiRequest(
    apiClient.post(`/accounts/${encodeURIComponent(accountId)}/tasks`, body, requestConfig(options)),
  )
}

export function getTask(taskId, options = {}) {
  return apiRequest(apiClient.get(taskPath(taskId), requestConfig(options)))
}

export function getTaskDetails(taskId, options = {}) {
  return apiRequest(apiClient.get(`${taskPath(taskId)}/details`, requestConfig(options)))
}

function cursorValue(options) {
  if (typeof options === 'number' || typeof options === 'string') return options
  return options?.after ?? 0
}

export function getTaskLogs(taskId, options = {}) {
  return apiRequest(
    apiClient.get(`${taskPath(taskId)}/logs`, {
      params: { after: cursorValue(options) },
      ...requestConfig(options),
    }),
  )
}

export function cancelTask(taskId, options = {}) {
  return apiRequest(apiClient.post(`${taskPath(taskId)}/cancel`, undefined, requestConfig(options)))
}

export const fetchTasks = listTasks
export const fetchTask = getTask
export const fetchTaskDetails = getTaskDetails
export const fetchTaskLogs = getTaskLogs
export const startAccountTask = startTask

export default {
  listTasks,
  startTask,
  getTask,
  getTaskDetails,
  getTaskLogs,
  cancelTask,
}
