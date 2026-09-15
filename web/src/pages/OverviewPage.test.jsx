import { StrictMode } from 'react'
import { act, createEvent, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, vi } from 'vitest'
import { ApiError } from '../api/client'
import { deleteAccount, listAccounts, setAccountEnabled, verifyAccount } from '../api/accounts'
import { listTasks } from '../api/tasks'
import OverviewPage from './OverviewPage'

function renderOverview(ui) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      {ui}
    </MemoryRouter>,
  )
}

vi.mock('../api/accounts', () => ({
  createAccount: vi.fn(),
  deleteAccount: vi.fn(),
  listAccounts: vi.fn(),
  setAccountEnabled: vi.fn(),
  updateAccount: vi.fn(),
  verifyAccount: vi.fn(),
}))

vi.mock('../api/tasks', () => ({
  listTasks: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  listAccounts.mockResolvedValue([{ id: 'a', name: '张三', username: '13800000000', enabled: true }])
  listTasks.mockResolvedValue([])
  setAccountEnabled.mockResolvedValue({ id: 'a', enabled: false })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })
})

test('empty state points to the sidebar without rendering a duplicate add action', async () => {
  listAccounts.mockResolvedValue([])
  listTasks.mockResolvedValue([])

  renderOverview(<OverviewPage />)

  expect(await screen.findByText('请使用侧栏底部的“添加账户”。')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /添加.*账户/ })).not.toBeInTheDocument()
})

test('uses one managed signal for the default account and task loads and aborts it on effect rerun', async () => {
  let resolveAccounts
  let resolveTasks
  let accountSignal
  let taskSignal
  listAccounts.mockImplementation((options) => {
    accountSignal = options?.signal
    return new Promise((resolve) => { resolveAccounts = resolve })
  })
  listTasks.mockImplementation((options) => {
    taskSignal = options?.signal
    return new Promise((resolve) => { resolveTasks = resolve })
  })

  const { rerender } = renderOverview(<OverviewPage />)

  expect(accountSignal).toBeInstanceOf(AbortSignal)
  expect(taskSignal).toBe(accountSignal)

  rerender(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <OverviewPage accounts={[{ id: 'new', name: '新账户' }]} tasks={[]} loading={false} />
    </MemoryRouter>,
  )

  expect(accountSignal.aborted).toBe(true)
  await act(async () => {
    resolveAccounts([{ id: 'old', name: '旧账户' }])
    resolveTasks([])
  })
  expect(screen.getByText('新账户')).toBeInTheDocument()
  expect(screen.queryByText('旧账户')).not.toBeInTheDocument()
})

test('aborts the shared overview load signal when unmounted', async () => {
  let resolveAccounts
  let resolveTasks
  let signal
  listAccounts.mockImplementation((options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveAccounts = resolve })
  })
  listTasks.mockImplementation((options) => new Promise((resolve) => { resolveTasks = resolve }))

  const { unmount } = renderOverview(<OverviewPage />)
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(false)

  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => {
    resolveAccounts([])
    resolveTasks([])
  })
})

test('does not show an error when the default overview load is aborted', async () => {
  const aborted = new Error('request cancelled')
  aborted.name = 'AbortError'
  listAccounts.mockRejectedValue(aborted)
  listTasks.mockResolvedValue([])

  renderOverview(<OverviewPage />)

  expect(await screen.findByText('还没有账户')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test.each([
  ['running', '运行中'],
  ['stopping', '正在停止'],
  ['failed', '失败'],
  ['completed', '已完成'],
  ['stopped', '已停止'],
])('renders %s account task state', async (state, label) => {
  listAccounts.mockResolvedValue([{ id: 'a', name: '张三', enabled: true }])
  listTasks.mockResolvedValue([
    {
      id: 't',
      account_id: 'a',
      state,
      progress: 1,
      total: 2,
      error: state === 'failed' ? '连接失败' : null,
    },
  ])

  renderOverview(<OverviewPage />)

  expect(await screen.findByText(label)).toBeInTheDocument()
  expect(screen.getByText('张三')).toBeInTheDocument()
})

test('confirms delete and keeps active account when backend rejects it', async () => {
  const user = userEvent.setup()
  deleteAccount.mockRejectedValue(new ApiError('账户正在运行', 409, 'account_active'))

  renderOverview(<OverviewPage />)

  await user.click(await screen.findByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  await user.click(screen.getByRole('button', { name: '确认删除' }))

  expect(await screen.findByText('请先停止该账户的任务')).toBeInTheDocument()
  expect(screen.getByText('张三')).toBeInTheDocument()
})

test('renders disabled accounts as distinct from an idle account', async () => {
  listAccounts.mockResolvedValue([
    { id: 'a', name: '张三', enabled: true },
    { id: 'b', name: '李四', enabled: false },
  ])

  renderOverview(<OverviewPage />)

  expect(await screen.findByText('空闲')).toBeInTheDocument()
  expect(screen.getByText('已停用')).toBeInTheDocument()
})

test('masks a phone username in the overview while preserving the full value in edit form', async () => {
  const user = userEvent.setup()
  listAccounts.mockResolvedValue([
    { id: 'a', name: '张三', username: '13800000000', enabled: true },
  ])

  renderOverview(<OverviewPage />)

  expect(await screen.findByText('138****0000')).toBeInTheDocument()
  expect(screen.queryByText('13800000000')).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '编辑账户' }))

  expect(screen.getByLabelText('手机号')).toHaveValue('13800000000')
})

test('renders a state-appropriate primary action with the actual task target', async () => {
  renderOverview(
    <OverviewPage
      accounts={[
        { id: 'idle-account', name: '空闲账号', enabled: true },
        { id: 'running-account', name: '运行账号', enabled: true },
        { id: 'failed-account', name: '失败账号', enabled: true },
      ]}
      tasks={[
        { id: 'running-task', account_id: 'running-account', state: 'running' },
        { id: 'failed-task', account_id: 'failed-account', state: 'failed', error: '登录失败' },
      ]}
      loading={false}
    />
  )

  expect(await screen.findByRole('link', { name: '配置并开始' })).toHaveAttribute(
    'href',
    '/accounts/idle-account/launch',
  )
  expect(screen.getByRole('link', { name: '查看任务' })).toHaveAttribute('href', '/tasks/running-task')
  expect(screen.getByRole('link', { name: '查看错误' })).toHaveAttribute('href', '/tasks/failed-task')
})

test('keeps More menu keyboard navigable and restores focus to its trigger', async () => {
  const user = userEvent.setup()
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(trigger)
  const menuItems = screen.getAllByRole('menuitem')
  expect(menuItems[0]).toHaveFocus()
  await user.keyboard('{ArrowDown}')
  expect(menuItems[1]).toHaveFocus()
  await user.keyboard('{Escape}')
  expect(trigger).toHaveFocus()
})

test('renders account action failures as a nearby danger alert', async () => {
  const user = userEvent.setup()
  verifyAccount.mockRejectedValue(new ApiError('验证失败，请重试', 401, 'account_invalid'))
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '重新验证' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('验证失败，请重试')
})

test('disables the current account menu during verification and ignores duplicate calls', async () => {
  const user = userEvent.setup()
  let resolveVerify
  verifyAccount.mockImplementation(() => new Promise((resolve) => { resolveVerify = resolve }))
  renderOverview(
    <OverviewPage
      accounts={[
        { id: 'a', name: '张三', enabled: true },
        { id: 'b', name: '李四', enabled: true },
      ]}
      tasks={[]}
      loading={false}
    />,
  )

  const currentTrigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(currentTrigger)
  const verifyItem = screen.getByRole('menuitem', { name: '重新验证' })
  await user.click(verifyItem)
  fireEvent.click(verifyItem)

  expect(verifyAccount).toHaveBeenCalledTimes(1)
  expect(currentTrigger).toBeDisabled()
  expect(screen.getByRole('button', { name: '李四的更多操作' })).not.toBeDisabled()

  await act(async () => { resolveVerify({ id: 'a', verification_status: 'valid' }) })
  expect(await screen.findByText('账户验证成功')).toBeInTheDocument()
  expect(currentTrigger).not.toBeDisabled()
})

test('disables the current account menu during enable or disable and ignores duplicate calls', async () => {
  const user = userEvent.setup()
  let resolveToggle
  setAccountEnabled.mockImplementation(() => new Promise((resolve) => { resolveToggle = resolve }))
  renderOverview(
    <OverviewPage
      accounts={[
        { id: 'a', name: '张三', enabled: true },
        { id: 'b', name: '李四', enabled: true },
      ]}
      tasks={[]}
      loading={false}
    />,
  )

  const currentTrigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(currentTrigger)
  const toggleItem = screen.getByRole('menuitem', { name: '停用账户' })
  await user.click(toggleItem)
  fireEvent.click(toggleItem)

  expect(setAccountEnabled).toHaveBeenCalledTimes(1)
  expect(currentTrigger).toBeDisabled()
  expect(screen.getByRole('button', { name: '李四的更多操作' })).not.toBeDisabled()

  await act(async () => { resolveToggle({ id: 'a', enabled: false }) })
  expect(await screen.findByText('账户已停用')).toBeInTheDocument()
  expect(currentTrigger).not.toBeDisabled()
})

test('keeps every delete dismissal control disabled and blocks dismissal while delete is pending', async () => {
  const user = userEvent.setup()
  let resolveDelete
  deleteAccount.mockImplementation(() => new Promise((resolve) => { resolveDelete = resolve }))
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  await user.click(screen.getByRole('button', { name: '确认删除' }))

  const dialog = screen.getByRole('dialog')
  expect(screen.getByRole('button', { name: '关闭删除确认对话框' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled()

  const escape = createEvent.keyDown(dialog, { key: 'Escape' })
  fireEvent(dialog, escape)
  expect(escape.defaultPrevented).toBe(true)
  expect(screen.getByRole('dialog')).toBeInTheDocument()

  const outside = createEvent.pointerDown(document.body)
  fireEvent(document.body, outside)
  expect(screen.getByRole('dialog')).toBeInTheDocument()

  await act(async () => { resolveDelete({}) })
  expect(await screen.findByText('账户已删除')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('makes overview pending status identify each account and action concurrently', async () => {
  const user = userEvent.setup()
  let resolveVerify
  let resolveToggle
  verifyAccount.mockImplementation(() => new Promise((resolve) => { resolveVerify = resolve }))
  setAccountEnabled.mockImplementation(() => new Promise((resolve) => { resolveToggle = resolve }))
  renderOverview(
    <OverviewPage
      accounts={[
        { id: 'a', name: '张三', enabled: true },
        { id: 'b', name: '李四', enabled: true },
      ]}
      tasks={[]}
      loading={false}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '重新验证' }))
  await user.click(screen.getByRole('button', { name: '李四的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '停用账户' }))

  const status = screen.getByTestId('overview-pending-status')
  expect(status).toHaveTextContent('正在验证账户“张三”')
  expect(status).toHaveTextContent('正在停用账户“李四”')

  await act(async () => {
    resolveVerify({ id: 'a', verification_status: 'valid' })
    resolveToggle({ id: 'b', enabled: false })
  })
})

test('uses the actual task identity for every task state without a full-page reload', async () => {
  const taskCases = [
    { account: 'running-account', state: 'running', task: { id: 'running-id' }, label: '查看任务' },
    { account: 'stopping-account', state: 'stopping', task: { task_id: 'stopping-id' }, label: '查看任务' },
    { account: 'completed-account', state: 'completed', task: { taskId: 'completed-id' }, label: '查看结果' },
    { account: 'stopped-account', state: 'stopped', task: { id: 'stopped-id' }, label: '查看结果' },
    { account: 'failed-account', state: 'failed', task: { task_id: 'failed-id' }, label: '查看错误' },
  ]
  const accounts = [
    { id: 'idle-account', name: '空闲账号', enabled: true },
    ...taskCases.map(({ account }) => ({ id: account, name: account, enabled: true })),
  ]
  const tasks = taskCases.map(({ account, state, task }) => ({
    ...task,
    account_id: account,
    state,
  }))

  renderOverview(<OverviewPage accounts={accounts} tasks={tasks} loading={false} />)

  expect(await screen.findByRole('link', { name: '配置并开始' })).toHaveAttribute(
    'href',
    '/accounts/idle-account/launch',
  )
  const links = screen.getAllByRole('link')
  for (const { task, label } of taskCases) {
    const taskId = task.id ?? task.task_id ?? task.taskId
    expect(links.find((link) => link.textContent === label && link.getAttribute('href') === `/tasks/${taskId}`)).toBeTruthy()
  }
})

test('does not report success when account verification returns an unverified profile', async () => {
  const user = userEvent.setup()
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'unverified' })
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '重新验证' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('账户验证失败，请重试')
  expect(screen.queryByText('账户验证成功')).not.toBeInTheDocument()
})

test('opening More does not steal focus before the user opens it, and Tab closes it', async () => {
  const user = userEvent.setup()
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  expect(trigger).not.toHaveFocus()
  await user.click(trigger)
  expect(screen.getByRole('menuitem', { name: '编辑账户' })).toHaveFocus()
  await user.tab()
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
})

test('outside dismissal closes More and restores the trigger focus', async () => {
  const user = userEvent.setup()
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(trigger)
  await user.click(screen.getByRole('heading', { name: '账户概览' }))

  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})

test('successful verification closes More and restores focus to its valid trigger', async () => {
  const user = userEvent.setup()
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(trigger)
  await user.click(screen.getByRole('menuitem', { name: '重新验证' }))

  await screen.findByText('账户验证成功')
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})

test('successful enable or disable closes More and restores its trigger focus', async () => {
  const user = userEvent.setup()
  setAccountEnabled.mockResolvedValue({ id: 'a', enabled: false })
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(trigger)
  await user.click(screen.getByRole('menuitem', { name: '停用账户' }))

  expect(await screen.findByText('账户已停用')).toBeInTheDocument()
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})

test('does not update parent state when verification resolves after unmount', async () => {
  const user = userEvent.setup()
  const onAccountsChange = vi.fn()
  let resolveVerify
  let signal
  verifyAccount.mockImplementation((_accountId, options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveVerify = resolve })
  })
  const { unmount } = renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
      onAccountsChange={onAccountsChange}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '重新验证' }))
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(false)

  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveVerify({ id: 'a', verification_status: 'valid' }) })

  expect(onAccountsChange).not.toHaveBeenCalled()
})

test('does not update parent state when enable or disable resolves after unmount', async () => {
  const user = userEvent.setup()
  const onAccountsChange = vi.fn()
  let resolveToggle
  let signal
  setAccountEnabled.mockImplementation((_accountId, _enabled, options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveToggle = resolve })
  })
  const { unmount } = renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
      onAccountsChange={onAccountsChange}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '停用账户' }))
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(false)

  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveToggle({ id: 'a', enabled: false }) })

  expect(onAccountsChange).not.toHaveBeenCalled()
})

test('does not update parent state when delete resolves after unmount', async () => {
  const user = userEvent.setup()
  const onAccountsChange = vi.fn()
  const onTasksChange = vi.fn()
  let resolveDelete
  let signal
  deleteAccount.mockImplementation((_accountId, options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveDelete = resolve })
  })
  const { unmount } = renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[{ id: 'task-a', account_id: 'a', state: 'completed' }]}
      loading={false}
      onAccountsChange={onAccountsChange}
      onTasksChange={onTasksChange}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  await user.click(screen.getByRole('button', { name: '确认删除' }))
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(false)

  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveDelete({}) })

  expect(onAccountsChange).not.toHaveBeenCalled()
  expect(onTasksChange).not.toHaveBeenCalled()
})

test('uses a ref guard to issue only one delete for consecutive stale confirmation clicks', async () => {
  const user = userEvent.setup()
  let resolveDelete
  deleteAccount.mockImplementation(() => new Promise((resolve) => { resolveDelete = resolve }))
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  const confirmButton = screen.getByRole('button', { name: '确认删除' })
  act(() => {
    fireEvent.click(confirmButton)
    fireEvent.click(confirmButton)
  })

  expect(deleteAccount).toHaveBeenCalledTimes(1)
  await act(async () => { resolveDelete({}) })
})

test('releases delete pending state after failure so the account can be retried', async () => {
  const user = userEvent.setup()
  deleteAccount
    .mockRejectedValueOnce(new ApiError('暂时无法删除', 500, 'delete_failed'))
    .mockResolvedValueOnce({})
  renderOverview(
    <OverviewPage
      accounts={[{ id: 'a', name: '张三', enabled: true }]}
      tasks={[]}
      loading={false}
    />,
  )

  await user.click(screen.getByRole('button', { name: '张三的更多操作' }))
  await user.click(screen.getByRole('menuitem', { name: '删除账户' }))
  const confirmButton = screen.getByRole('button', { name: '确认删除' })
  await user.click(confirmButton)

  expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法删除')
  expect(confirmButton).not.toBeDisabled()
  await user.click(confirmButton)

  expect(await screen.findByText('账户已删除')).toBeInTheDocument()
  expect(deleteAccount).toHaveBeenCalledTimes(2)
})

test('keeps More menu focus behavior under React StrictMode', async () => {
  const user = userEvent.setup()
  renderOverview(
    <StrictMode>
      <OverviewPage
        accounts={[{ id: 'a', name: '张三', enabled: true }]}
        tasks={[]}
        loading={false}
      />
    </StrictMode>,
  )

  const trigger = screen.getByRole('button', { name: '张三的更多操作' })
  await user.click(trigger)
  expect(screen.getByRole('menuitem', { name: '编辑账户' })).toHaveFocus()

  await user.keyboard('{Escape}')
  expect(trigger).toHaveFocus()

  await user.click(trigger)
  await user.click(screen.getByRole('heading', { name: '账户概览' }))
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()

  await user.click(trigger)
  await user.click(screen.getByRole('menuitem', { name: '停用账户' }))
  expect(await screen.findByText('账户已停用')).toBeInTheDocument()
  expect(trigger).toHaveFocus()
})
