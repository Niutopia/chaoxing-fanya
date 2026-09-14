import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, vi } from 'vitest'
import { ApiError } from '../api/client'
import { getTask, getTaskDetails, getTaskLogs, cancelTask } from '../api/tasks'
import TaskPage from './TaskPage'

vi.mock('../api/tasks', () => ({
  getTask: vi.fn(),
  getTaskDetails: vi.fn(),
  getTaskLogs: vi.fn(),
  cancelTask: vi.fn(),
}))

const runningSnapshot = {
  id: 'task-a',
  account_id: 'account-a',
  state: 'running',
  progress: 1,
  total: 3,
}

function renderPage(path = '/tasks/task-a', props = {}) {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskPage {...props} />} />
      </Routes>
    </MemoryRouter>,
  )
}

async function flushInitialPoll() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  getTask.mockResolvedValue(runningSnapshot)
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  getTaskLogs.mockResolvedValue({ items: [], next_cursor: 0 })
  cancelTask.mockResolvedValue({ ...runningSnapshot, state: 'stopping' })
})

test('renders only logs returned for the selected task', async () => {
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state: 'running', progress: 1, total: 2 })
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  getTaskLogs.mockResolvedValue({
    items: [{ sequence: 1, level: 'info', message: 'account-a-only', timestamp: 1789371508 }],
    next_cursor: 1,
  })
  renderPage('/tasks/task-a')
  expect(await screen.findByText('account-a-only')).toBeInTheDocument()
  expect(screen.queryByText('account-b-only')).not.toBeInTheDocument()
})

test('stop changes to a noninteractive stopping state', async () => {
  const user = userEvent.setup()
  cancelTask.mockResolvedValue({ id: 'task-a', state: 'stopping' })
  renderPage('/tasks/task-a')
  await user.click(await screen.findByRole('button', { name: '停止任务' }))
  expect(screen.getByRole('button', { name: '正在停止' })).toBeDisabled()
})

test.each([
  ['completed', '已完成'],
  ['failed', '失败'],
  ['stopped', '已停止'],
])('stops polling for terminal state %s', async (state, label) => {
  vi.useFakeTimers()
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state })
  renderPage('/tasks/task-a')
  await flushInitialPoll()
  expect(screen.getByText(label)).toBeInTheDocument()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000)
  })
  expect(getTask).toHaveBeenCalledTimes(1)
})

test('retains snapshot while reconnecting and recovers on the next poll', async () => {
  vi.useFakeTimers()
  getTask
    .mockResolvedValueOnce(runningSnapshot)
    .mockRejectedValueOnce(new ApiError('offline', 503, 'network_error'))
    .mockResolvedValueOnce({ ...runningSnapshot, progress: 2 })
  renderPage('/tasks/task-a')
  await flushInitialPoll()
  expect(screen.getByText('1 / 3')).toBeInTheDocument()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(screen.getByText('正在重新连接')).toBeInTheDocument()
  expect(screen.getByText('1 / 3')).toBeInTheDocument()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(screen.queryByText('正在重新连接')).not.toBeInTheDocument()
  expect(screen.getByText('2 / 3')).toBeInTheDocument()
})

test('appends unique log sequences and expands course details', async () => {
  vi.useFakeTimers()
  getTaskLogs
    .mockResolvedValueOnce({ items: [{ sequence: 1, level: 'info', message: 'first', timestamp: 1 }], next_cursor: 1 })
    .mockResolvedValueOnce({ items: [{ sequence: 2, level: 'info', message: 'second', timestamp: 2 }], next_cursor: 2 })
  getTaskDetails.mockResolvedValue({
    courses: [{ id: 'math', title: '高等数学', chapters: [{ id: 'one', title: '第一章', status: 'completed' }] }],
    active_jobs: { video: { job_name: '教学视频', progress: 50, current_time: 30, duration: 60 } },
  })
  renderPage('/tasks/task-a')
  await flushInitialPoll()
  fireEvent.click(screen.getByRole('button', { name: '展开高等数学' }))
  expect(screen.getByText('第一章')).toBeInTheDocument()
  expect(screen.getByRole('progressbar', { name: '教学视频' })).toHaveAttribute('aria-valuenow', '50')
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(screen.getAllByText('first')).toHaveLength(1)
  expect(screen.getByText('second')).toBeInTheDocument()
})

test('unknown task links back to overview and launch', async () => {
  getTask.mockRejectedValue(new ApiError('不存在', 404, 'task_not_found'))
  renderPage('/tasks/missing')
  expect(await screen.findByRole('link', { name: '返回任务总览' })).toHaveAttribute('href', '/')
})
