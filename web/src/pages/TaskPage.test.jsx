import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
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

function NavigableRoutes({ onReady, props }) {
  const navigate = useNavigate()

  useEffect(() => {
    onReady(navigate)
  }, [navigate, onReady])

  return (
    <Routes>
      <Route path="/tasks/:taskId" element={<TaskPage {...props} />} />
    </Routes>
  )
}

function renderNavigablePage(path = '/tasks/task-a', props = {}) {
  let navigateTo
  const result = render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <NavigableRoutes onReady={(navigate) => { navigateTo = navigate }} props={props} />
    </MemoryRouter>,
  )
  return {
    ...result,
    navigate: (nextPath) => act(async () => {
      navigateTo(nextPath)
      await Promise.resolve()
    }),
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
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
  expect(screen.getByRole('dialog', { name: '确认停止任务' })).toBeInTheDocument()
  expect(cancelTask).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '停止任务', hidden: true })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: '确认停止' }))
  expect(screen.getByRole('button', { name: '正在停止' })).toBeDisabled()
  expect(cancelTask).toHaveBeenCalledTimes(1)
})

test('restores focus to the overview link when successful cancellation disables the trigger', async () => {
  const user = userEvent.setup()
  cancelTask.mockResolvedValue({ ...runningSnapshot, state: 'stopping' })
  renderPage('/tasks/task-a')

  const trigger = await screen.findByRole('button', { name: '停止任务' })
  const overviewLink = screen.getByRole('link', { name: '返回任务总览' })
  await user.click(trigger)
  await user.click(screen.getByRole('button', { name: '确认停止' }))

  expect(cancelTask).toHaveBeenCalledTimes(1)
  expect(await screen.findByRole('button', { name: '正在停止' })).toBeDisabled()
  expect(trigger).toBeDisabled()
  expect(overviewLink).toHaveFocus()
})

test('restores trigger focus and closes the cancel dialog with Escape', async () => {
  const user = userEvent.setup()
  renderPage('/tasks/task-a')

  const trigger = await screen.findByRole('button', { name: '停止任务' })
  await user.click(trigger)
  expect(screen.getByRole('button', { name: '继续运行' })).toHaveFocus()
  expect(screen.getByRole('heading', { name: '确认停止任务' })).toBeInTheDocument()
  expect(screen.getByRole('dialog', { name: '确认停止任务' })).toHaveAccessibleDescription(
    '将停止账户“account-a”正在处理的课程“当前课程”。',
  )

  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: '确认停止任务' })).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
  expect(cancelTask).not.toHaveBeenCalled()
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
  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent('1 / 3')
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(screen.getByText('正在重新连接')).toBeInTheDocument()
  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent('1 / 3')
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
  expect(screen.queryByText('正在重新连接')).not.toBeInTheDocument()
  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent('2 / 3')
})

test('appends unique log sequences and expands course details', async () => {
  vi.useFakeTimers()
  getTaskLogs
    .mockResolvedValueOnce({ items: [{ sequence: 1, level: 'info', message: 'first', timestamp: 1 }], next_cursor: 1 })
    .mockResolvedValueOnce({
      items: [
        { sequence: 1, level: 'info', message: 'first duplicate', timestamp: 1 },
        { sequence: 2, level: 'info', message: 'second', timestamp: 2 },
      ],
      next_cursor: 2,
    })
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
  expect(screen.queryByText('first duplicate')).not.toBeInTheDocument()
  expect(screen.getByText('second')).toBeInTheDocument()
  expect(getTaskLogs).toHaveBeenNthCalledWith(2, 'task-a', { after: 1 })
})

test('resets cursor and logs when the selected task route changes', async () => {
  getTask.mockImplementation((taskId) => Promise.resolve({
    id: taskId,
    account_id: taskId === 'task-a' ? 'account-a' : 'account-b',
    state: 'running',
    progress: taskId === 'task-a' ? 1 : 4,
    total: 4,
  }))
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  getTaskLogs.mockImplementation((taskId, options) => Promise.resolve({
    items: [{
      sequence: 1,
      level: 'info',
      message: taskId === 'task-a' ? 'task-a-log' : 'task-b-log',
      timestamp: 1,
    }],
    next_cursor: taskId === 'task-a' ? 7 : 3,
  }))

  const { navigate } = renderNavigablePage('/tasks/task-a')
  expect(await screen.findByText('task-a-log')).toBeInTheDocument()
  await navigate('/tasks/task-b')
  expect(await screen.findByText('task-b-log')).toBeInTheDocument()
  expect(screen.queryByText('task-a-log')).not.toBeInTheDocument()

  const taskBCalls = getTaskLogs.mock.calls.filter(([taskId]) => taskId === 'task-b')
  expect(taskBCalls[0]?.[1]).toEqual({ after: 0 })
})

test('ignores a stale cancel success after changing to another task', async () => {
  const user = userEvent.setup()
  const pendingCancel = deferred()
  const onSnapshot = vi.fn()
  getTask.mockImplementation((taskId) => Promise.resolve({
    id: taskId,
    account_id: taskId === 'task-a' ? 'account-a' : 'account-b',
    state: 'running',
    progress: taskId === 'task-a' ? 1 : 9,
    total: 10,
  }))
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  cancelTask.mockReturnValue(pendingCancel.promise)

  const { navigate } = renderNavigablePage('/tasks/task-a', { onSnapshot })
  expect(await screen.findByLabelText('已完成课程数量')).toHaveTextContent('1 / 10')
  onSnapshot.mockClear()
  await user.click(screen.getByRole('button', { name: '停止任务' }))
  await user.click(screen.getByRole('button', { name: '确认停止' }))
  expect(cancelTask).toHaveBeenCalledWith('task-a')

  await navigate('/tasks/task-b')
  expect(await screen.findByLabelText('已完成课程数量')).toHaveTextContent('9 / 10')
  pendingCancel.resolve({
    id: 'task-a',
    account_id: 'account-a',
    state: 'stopping',
    progress: 10,
    total: 10,
  })
  await flushInitialPoll()

  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent('9 / 10')
  expect(screen.getByLabelText('已完成课程数量')).not.toHaveTextContent('10 / 10')
  expect(onSnapshot.mock.calls.some(([snapshot]) => snapshot.id === 'task-a' && snapshot.state === 'stopping')).toBe(false)
})

test('ignores a stale cancel rejection while the next task is stopping', async () => {
  const user = userEvent.setup()
  const pendingA = deferred()
  const pendingB = deferred()
  getTask.mockImplementation((taskId) => Promise.resolve({
    id: taskId,
    account_id: taskId === 'task-a' ? 'account-a' : 'account-b',
    state: 'running',
    progress: 1,
    total: 2,
  }))
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {} })
  cancelTask.mockImplementation((taskId) => taskId === 'task-a' ? pendingA.promise : pendingB.promise)

  const { navigate } = renderNavigablePage('/tasks/task-a')
  expect(await screen.findByLabelText('已完成课程数量')).toHaveTextContent('1 / 2')
  await user.click(screen.getByRole('button', { name: '停止任务' }))
  await user.click(screen.getByRole('button', { name: '确认停止' }))
  await navigate('/tasks/task-b')
  expect(await screen.findByLabelText('已完成课程数量')).toHaveTextContent('1 / 2')
  await user.click(screen.getByRole('button', { name: '停止任务' }))
  await user.click(screen.getByRole('button', { name: '确认停止' }))
  expect(cancelTask).toHaveBeenCalledTimes(2)

  pendingA.reject(new ApiError('cancel A failed', 503, 'network_error'))
  await flushInitialPoll()

  expect(screen.getByRole('button', { name: '正在停止', hidden: true })).toBeDisabled()
  expect(screen.getByRole('button', { name: '确认停止' })).toBeDisabled()
  expect(screen.queryByText('cancel A failed')).not.toBeInTheDocument()
  pendingB.resolve({ id: 'task-b', account_id: 'account-b', state: 'stopping', progress: 1, total: 2 })
  await flushInitialPoll()
})

test('renders elapsed time and aggregate course, chapter, and task counts', async () => {
  const startedAt = Math.floor(Date.now() / 1000) - 125
  getTask.mockResolvedValue({
    id: 'task-a',
    account_id: 'account-a',
    state: 'completed',
    progress: 2,
    total: 2,
    started_at: startedAt,
    finished_at: startedAt + 65,
    stats: { completed_courses: 1, total_courses: 2 },
  })
  getTaskDetails.mockResolvedValue({
    courses: [
      { id: 'math', title: '高等数学', status: 'completed', chapters: [
        { id: 'one', title: '第一章', status: 'completed' },
        { id: 'two', title: '第二章', status: 'pending' },
      ] },
      { id: 'english', title: '大学英语', status: 'pending', chapters: [
        { id: 'three', title: '第三章', status: 'pending' },
      ] },
    ],
    active_jobs: { video: { job_name: '教学视频', progress: 100 } },
    counts: {
      completed_courses: 1,
      total_courses: 2,
      completed_chapters: 3,
      total_chapters: 5,
      completed_tasks: 4,
      total_tasks: 8,
    },
  })
  renderPage('/tasks/task-a')

  expect(await screen.findByLabelText('任务已用时间')).toHaveTextContent('1:05')
  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent('1 / 2')
  expect(screen.getByLabelText('已完成章节数量')).toHaveTextContent('3 / 5')
  expect(screen.getByLabelText('已完成任务数量')).toHaveTextContent('4 / 8')
})

test('uses fallback values for missing elapsed and count fields', async () => {
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state: 'failed' })
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {}, counts: {} })
  renderPage('/tasks/task-a')

  expect(await screen.findByLabelText('任务已用时间')).toHaveTextContent('—')
  expect(screen.getByLabelText('已完成课程数量')).toHaveTextContent(/^—$/)
  expect(screen.getByLabelText('已完成章节数量')).toHaveTextContent(/^—$/)
  expect(screen.getByLabelText('已完成任务数量')).toHaveTextContent(/^—$/)
})

test('uses snapshot progress as the live course completed fallback', async () => {
  getTask.mockResolvedValue({
    id: 'task-a',
    account_id: 'account-a',
    state: 'running',
    progress: 1,
    total: 3,
  })
  getTaskDetails.mockResolvedValue({ courses: [], active_jobs: {}, counts: {} })
  renderPage('/tasks/task-a')

  expect(await screen.findByLabelText('已完成课程数量')).toHaveTextContent(/^1 \/ 3$/)
  expect(screen.getByLabelText('已完成章节数量')).toHaveTextContent(/^—$/)
  expect(screen.getByLabelText('已完成任务数量')).toHaveTextContent(/^—$/)
})

test.each([
  ['completed', '已完成'],
  ['failed', '失败'],
  ['stopped', '已停止'],
])('terminal %s tasks link directly to the owning account launch page', async (state, label) => {
  getTask.mockResolvedValue({ id: 'task-a', account_id: 'account-a', state })
  renderPage('/tasks/task-a')

  expect(await screen.findByText(label)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '返回课程启动' })).toHaveAttribute(
    'href',
    '/accounts/account-a/launch',
  )
})

test('unknown task links back to overview and launch', async () => {
  getTask.mockRejectedValue(new ApiError('不存在', 404, 'task_not_found'))
  renderPage('/tasks/missing')
  expect(await screen.findByRole('link', { name: '返回任务总览' })).toHaveAttribute('href', '/')
})
