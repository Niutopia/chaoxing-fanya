import { render, screen } from '@testing-library/react'
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

test('empty state focuses the add-account action', async () => {
  listAccounts.mockResolvedValue([])
  listTasks.mockResolvedValue([])

  renderOverview(<OverviewPage />)

  expect(await screen.findByRole('button', { name: '添加第一个账户' })).toHaveFocus()
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
