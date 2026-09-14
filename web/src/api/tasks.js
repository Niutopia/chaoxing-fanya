import apiClient, { apiRequest } from './client'

const taskPath = (taskId) => `/tasks/${encodeURIComponent(taskId)}`

export function listTasks() {
  return apiRequest(apiClient.get('/tasks'))
}

export function startTask(accountId, payload = {}) {
  const body = Array.isArray(payload) ? { course_ids: payload } : payload
  return apiRequest(
    apiClient.post(`/accounts/${encodeURIComponent(accountId)}/tasks`, body),
  )
}

export function getTask(taskId) {
  return apiRequest(apiClient.get(taskPath(taskId)))
}

export function getTaskDetails(taskId) {
  return apiRequest(apiClient.get(`${taskPath(taskId)}/details`))
}

function cursorValue(options) {
  if (typeof options === 'number' || typeof options === 'string') return options
  return options?.after ?? 0
}

export function getTaskLogs(taskId, options = {}) {
  return apiRequest(
    apiClient.get(`${taskPath(taskId)}/logs`, {
      params: { after: cursorValue(options) },
    }),
  )
}

export function cancelTask(taskId) {
  return apiRequest(apiClient.post(`${taskPath(taskId)}/cancel`))
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
