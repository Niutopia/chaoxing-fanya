import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { useState } from 'react'
import { beforeEach, expect, vi } from 'vitest'
import { ApiError } from '../api/client'
import { getPreferences, listCourses, savePreferences } from '../api/accounts'
import { startTask } from '../api/tasks'
import { getAnswerConnection } from '../api/settings'
import LaunchPage from './LaunchPage'

vi.mock('../api/accounts', () => ({
  getPreferences: vi.fn(),
  listCourses: vi.fn(),
  savePreferences: vi.fn(),
}))

vi.mock('../api/tasks', () => ({
  startTask: vi.fn(),
}))

vi.mock('../api/settings', () => ({
  getAnswerConnection: vi.fn(),
}))

const courseFixtures = [
  { courseId: 'math', title: '高等数学' },
  { courseId: 'english', title: '大学英语' },
  { courseId: 'history', title: '中国近现代史' },
]

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function SwitchableLaunch({ onSwitch }) {
  const [accountId, setAccountId] = useState('account-a')
  return (
    <>
      <button type="button" onClick={() => {
        setAccountId('account-b')
        onSwitch?.()
      }}>切换账户</button>
      <LaunchPage accountId={accountId} />
    </>
  )
}

function renderPage(path = '/accounts/account-a/launch', props = {}) {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/accounts/:accountId/launch" element={<LaunchPage {...props} />} />
        <Route path="/tasks/:taskId" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  listCourses.mockResolvedValue(courseFixtures)
  getPreferences.mockResolvedValue({
    selected_course_ids: [],
    speed: 1.5,
    jobs: 4,
    notopen_action: 'retry',
    answer_enabled: false,
  })
  savePreferences.mockResolvedValue({})
  getAnswerConnection.mockResolvedValue({
    enabled: false,
    last_test_status: 'untested',
  })
})

test('keeps course choices scoped to the current account', async () => {
  listCourses.mockResolvedValue([
    { courseId: 'math', title: '高等数学' },
    { courseId: 'english', title: '大学英语' },
  ])
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], speed: 1.5, jobs: 4, notopen_action: 'retry' })

  renderPage('/accounts/account-a/launch')

  expect(await screen.findByRole('checkbox', { name: '高等数学' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: '大学英语' })).not.toBeChecked()
  expect(listCourses).toHaveBeenCalledWith('account-a', { signal: expect.any(AbortSignal) })
  expect(getPreferences).toHaveBeenCalledWith('account-a', { signal: expect.any(AbortSignal) })
})

test('blocks start when answering is enabled but connection is untested', async () => {
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], answer_enabled: true })
  getAnswerConnection.mockResolvedValue({ enabled: true, last_test_status: 'untested' })

  renderPage('/accounts/account-a/launch')

  expect(await screen.findByText('请先在设置中测试答题连接')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始学习' })).toBeDisabled()
})

test.each([
  {},
  { last_test_status: 'pending' },
  { last_test_status: 'failed' },
])('treats missing, unknown, and failed answer status as untested', async (status) => {
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], answer_enabled: true })
  getAnswerConnection.mockResolvedValue({ enabled: true, has_api_key: true, ...status })

  renderPage('/accounts/account-a/launch')

  expect(await screen.findByText('请先在设置中测试答题连接')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始学习' })).toBeDisabled()
})

test('search select-all clear and stale refresh preserve the last course list', async () => {
  const user = userEvent.setup()
  listCourses.mockResolvedValueOnce(courseFixtures).mockRejectedValueOnce(new ApiError('网络错误', 503, 'courses_unavailable'))
  renderPage('/accounts/account-a/launch')

  await user.type(await screen.findByRole('searchbox', { name: '搜索课程' }), '英语')
  expect(screen.getByText('大学英语')).toBeInTheDocument()
  expect(screen.queryByText('高等数学')).not.toBeInTheDocument()
  await user.clear(screen.getByRole('searchbox', { name: '搜索课程' }))
  await user.click(screen.getByRole('button', { name: '全选' }))
  expect(screen.getAllByRole('checkbox', { checked: true })).toHaveLength(courseFixtures.length)
  await user.click(screen.getByRole('button', { name: '清空' }))
  await user.click(screen.getByRole('button', { name: '刷新课程' }))
  expect(await screen.findByText('正在显示上次成功加载的课程')).toBeInTheDocument()
  expect(screen.getByText('高等数学')).toBeInTheDocument()
})

test('saves preferences and navigates after successful start', async () => {
  const user = userEvent.setup()
  startTask.mockResolvedValue({ id: 'task-a' })
  renderPage('/accounts/account-a/launch')

  await user.click(await screen.findByRole('checkbox', { name: '高等数学' }))
  await user.click(screen.getByRole('button', { name: '开始学习' }))

  expect(savePreferences.mock.invocationCallOrder[0]).toBeLessThan(startTask.mock.invocationCallOrder[0])
  expect(startTask).toHaveBeenCalledWith(
    'account-a',
    { course_ids: ['math'] },
    { signal: expect.any(AbortSignal) },
  )
  expect(await screen.findByTestId('location')).toHaveTextContent('/tasks/task-a')
})

test('sends launch-only preferences without retaining advanced config', async () => {
  const user = userEvent.setup()
  startTask.mockResolvedValue({ id: 'task-a' })
  getPreferences.mockResolvedValue({
    selected_course_ids: [],
    speed: 1.5,
    jobs: 4,
    answer_enabled: false,
    notification_config: { token: 'launch-notification-secret' },
    ocr_config: { api_key: 'launch-ocr-secret', endpoint: 'http://ocr.example.invalid/v1' },
  })

  renderPage('/accounts/account-a/launch')
  await user.click(await screen.findByRole('checkbox', { name: '高等数学' }))
  await user.click(screen.getByRole('button', { name: '开始学习' }))

  const payload = savePreferences.mock.calls[0][1]
  expect(payload).not.toHaveProperty('notification_config')
  expect(payload).not.toHaveProperty('ocr_config')
  expect(JSON.stringify(payload)).not.toContain('launch-notification-secret')
  expect(JSON.stringify(payload)).not.toContain('launch-ocr-secret')
})

test('keeps start disabled while preference and connection requests are pending', async () => {
  const user = userEvent.setup()
  let resolveCourses
  let resolvePreferences
  let resolveConnection
  listCourses.mockReturnValue(new Promise((resolve) => { resolveCourses = resolve }))
  getPreferences.mockReturnValue(new Promise((resolve) => { resolvePreferences = resolve }))
  getAnswerConnection.mockReturnValue(new Promise((resolve) => { resolveConnection = resolve }))

  renderPage('/accounts/account-a/launch')
  resolveCourses(courseFixtures)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '高等数学' })).toBeDisabled())

  resolvePreferences({ selected_course_ids: ['math'], answer_enabled: false })
  await waitFor(() => expect(screen.getByRole('button', { name: '开始学习' })).toBeDisabled())
  resolveConnection({ enabled: false, last_test_status: 'untested' })
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '高等数学' })).not.toBeDisabled())
  expect(screen.getByRole('button', { name: '开始学习' })).not.toBeDisabled()
  await user.click(screen.getByRole('button', { name: '开始学习' }))
  expect(savePreferences).toHaveBeenCalled()
})

test('blocks start and preserves no default preference write when a dependency fails', async () => {
  getPreferences.mockRejectedValue(new ApiError('参数加载失败', 503, 'preferences_unavailable'))
  renderPage('/accounts/account-a/launch')

  expect(await screen.findByText('参数加载失败')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始学习' })).toBeDisabled()
  expect(savePreferences).not.toHaveBeenCalled()
  expect(startTask).not.toHaveBeenCalled()
})

test.each([
  [new ApiError('账户已有任务', 409, 'account_active'), '该账户已有任务在运行'],
  [new ApiError('账户已停用', 409, 'account_disabled'), '请先启用该账户'],
])('shows start conflict without losing selection', async (error, message) => {
  getPreferences.mockResolvedValue({ selected_course_ids: ['math'], answer_enabled: false })
  startTask.mockRejectedValue(error)
  renderPage('/accounts/account-a/launch')
  await userEvent.click(await screen.findByRole('button', { name: '开始学习' }))
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: '高等数学' })).toBeChecked()
})

test('guards a disabled account before saving or starting', async () => {
  const user = userEvent.setup()
  renderPage('/accounts/account-a/launch', { account: { id: 'account-a', enabled: false } })

  await user.click(await screen.findByRole('button', { name: '开始学习' }))

  expect(await screen.findByText('请先启用该账户')).toBeInTheDocument()
  expect(savePreferences).not.toHaveBeenCalled()
  expect(startTask).not.toHaveBeenCalled()
})

test('keeps a selection when starting fails for a validation error', async () => {
  const user = userEvent.setup()
  getPreferences.mockResolvedValue({ selected_course_ids: [], answer_enabled: false })
  renderPage('/accounts/account-a/launch')

  await user.click(await screen.findByRole('button', { name: '开始学习' }))

  expect(await screen.findByText('请至少选择一门课程')).toBeInTheDocument()
  await waitFor(() => expect(startTask).not.toHaveBeenCalled())
})

test('does not start the old account after its preference save resolves late', async () => {
  const user = userEvent.setup()
  let resolveSave
  savePreferences.mockImplementation(() => new Promise((resolve) => {
    resolveSave = resolve
  }))
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SwitchableLaunch />
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole('checkbox', { name: '高等数学' }))
  await user.click(screen.getByRole('button', { name: '开始学习' }))
  expect(savePreferences).toHaveBeenCalledWith(
    'account-a',
    expect.any(Object),
    { signal: expect.any(AbortSignal) },
  )
  const oldAccountSignal = savePreferences.mock.calls[0][2].signal
  expect(oldAccountSignal.aborted).toBe(false)

  await user.click(screen.getByRole('button', { name: '切换账户' }))
  expect(oldAccountSignal.aborted).toBe(true)
  resolveSave({})
  await waitFor(() => expect(startTask).not.toHaveBeenCalled())
  expect(screen.queryByRole('checkbox', { name: '高等数学' })).not.toBeChecked()
})
