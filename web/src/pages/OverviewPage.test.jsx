import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, vi } from 'vitest'
import { ApiError } from '../api/client'
import { deleteAccount, listAccounts, setAccountEnabled, verifyAccount } from '../api/accounts'
import { listTasks } from '../api/tasks'
import OverviewPage from './OverviewPage'

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

  render(<OverviewPage />)

  expect(await screen.findByRole('button', { name: '添加第一个账户' })).toHaveFocus()
})

test.each([
  ['running', '运行中'],
  ['stopping', '正在停止'],
  ['failed', '失败'],
  ['completed', '已完成'],
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

  render(<OverviewPage />)

  expect(await screen.findByText(label)).toBeInTheDocument()
  expect(screen.getByText('张三')).toBeInTheDocument()
})

test('confirms delete and keeps active account when backend rejects it', async () => {
  const user = userEvent.setup()
  deleteAccount.mockRejectedValue(new ApiError('账户正在运行', 409, 'account_active'))

  render(<OverviewPage />)

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

  render(<OverviewPage />)

  expect(await screen.findByText('空闲')).toBeInTheDocument()
  expect(screen.getByText('已停用')).toBeInTheDocument()
})

