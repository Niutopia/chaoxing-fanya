import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { createAccount, updateAccount, verifyAccount } from '../../api/accounts'
import AccountDialog from './AccountDialog'

vi.mock('../../api/accounts', () => ({
  createAccount: vi.fn(),
  updateAccount: vi.fn(),
  verifyAccount: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

test('editing an account never prefills its stored secret', async () => {
  render(
    <AccountDialog
      open
      account={{ id: 'a', name: '张三', username: '13800000000', has_secret: true }}
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  expect(screen.getByLabelText('密码')).toHaveValue('')
  expect(screen.getByText('已保存密码；留空表示不修改')).toBeInTheDocument()
})

test('submits a new account and then verifies it', async () => {
  const user = userEvent.setup()
  createAccount.mockResolvedValue({ id: 'a', name: '张三' })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })

  render(
    <AccountDialog
      open
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('密码'), 'local-secret')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))

  expect(await screen.findByText('账户验证成功')).toBeInTheDocument()
  expect(createAccount).toHaveBeenCalledWith({
    name: '张三',
    username: '13800000000',
    password: 'local-secret',
  })
  expect(verifyAccount).toHaveBeenCalledWith('a')
})

test('editing with a blank password sends no replacement secret', async () => {
  const user = userEvent.setup()
  updateAccount.mockResolvedValue({ id: 'a', name: '新名称', username: '13800000000' })

  render(
    <AccountDialog
      open
      account={{ id: 'a', name: '张三', username: '13800000000', has_secret: true }}
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  await user.clear(screen.getByLabelText('账户名称'))
  await user.type(screen.getByLabelText('账户名称'), '新名称')
  await user.click(screen.getByRole('button', { name: '保存账户' }))

  expect(updateAccount).toHaveBeenCalledWith('a', {
    name: '新名称',
    username: '13800000000',
  })
})

test('cookie mode enables only Cookie Header and omits a password replacement', async () => {
  const user = userEvent.setup()
  createAccount.mockResolvedValue({ id: 'a', name: '张三', username: '13800000000' })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })

  render(
    <AccountDialog
      open
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  await user.selectOptions(screen.getByLabelText('认证方式'), 'cookies')
  expect(screen.getByLabelText('密码')).toBeDisabled()
  expect(screen.getByLabelText('Cookie Header')).toBeEnabled()
  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('Cookie Header'), 'session=known-cookie')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))

  expect(createAccount).toHaveBeenCalledWith({
    name: '张三',
    username: '13800000000',
    cookies: 'session=known-cookie',
  })
  expect(JSON.stringify(createAccount.mock.calls)).not.toContain('password')
})

test('switching auth mode clears the inactive secret before a retry', async () => {
  const user = userEvent.setup()
  createAccount.mockResolvedValue({ id: 'a', name: '张三', username: '13800000000' })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'valid' })

  render(
    <AccountDialog
      open
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  await user.type(screen.getByLabelText('密码'), 'known-password')
  await user.selectOptions(screen.getByLabelText('认证方式'), 'cookies')
  expect(screen.getByLabelText('密码')).toHaveValue('')
  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('Cookie Header'), 'session=known-cookie')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))

  expect(createAccount).toHaveBeenCalledWith({
    name: '张三',
    username: '13800000000',
    cookies: 'session=known-cookie',
  })
  expect(JSON.stringify(createAccount.mock.calls)).not.toContain('known-password')
})

test('create once then verify failure transitions to safe revalidation without retaining the secret', async () => {
  const user = userEvent.setup()
  createAccount.mockResolvedValue({
    id: 'a',
    name: '张三',
    username: '13800000000',
    has_secret: true,
  })
  verifyAccount
    .mockRejectedValueOnce(new ApiError('登录失效', 401, 'account_invalid'))
    .mockResolvedValueOnce({ id: 'a', verification_status: 'valid' })

  render(
    <AccountDialog
      open
      onOpenChange={() => {}}
      onSaved={() => {}}
    />,
  )

  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('密码'), 'known-password')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))

  expect(await screen.findByText('登录失效')).toBeInTheDocument()
  expect(createAccount).toHaveBeenCalledTimes(1)
  expect(verifyAccount).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: '验证账户' })).toBeInTheDocument()
  expect(screen.getByLabelText('密码')).toHaveValue('')
  expect(document.body.innerHTML).not.toContain('known-password')

  await user.click(screen.getByRole('button', { name: '验证账户' }))

  expect(await screen.findByText('账户验证成功')).toBeInTheDocument()
  expect(createAccount).toHaveBeenCalledTimes(1)
  expect(verifyAccount).toHaveBeenCalledTimes(2)
})

test('saving an unverified account after a failed verify never reports it as verified', async () => {
  const user = userEvent.setup()
  const onSaved = vi.fn()
  updateAccount.mockResolvedValue({
    id: 'a',
    name: '新名称',
    username: '13800000000',
    verification_status: 'unverified',
  })

  render(
    <AccountDialog
      open
      account={{
        id: 'a',
        name: '张三',
        username: '13800000000',
        has_secret: true,
        verification_status: 'unverified',
      }}
      onOpenChange={() => {}}
      onSaved={onSaved}
    />
  )

  await user.clear(screen.getByLabelText('账户名称'))
  await user.type(screen.getByLabelText('账户名称'), '新名称')
  await user.click(screen.getByRole('button', { name: '保存账户' }))

  expect(await screen.findByText('账户已保存')).toBeInTheDocument()
  expect(onSaved).toHaveBeenCalledWith(
    expect.objectContaining({ id: 'a' }),
    expect.objectContaining({ persisted: true, verified: false }),
  )
})

test('a malformed verification response keeps a newly saved account unverified', async () => {
  const user = userEvent.setup()
  const onSaved = vi.fn()
  createAccount.mockResolvedValue({ id: 'a', name: '张三', username: '13800000000' })
  verifyAccount.mockResolvedValue({ id: 'a', verification_status: 'unverified' })

  render(
    <AccountDialog
      open
      onOpenChange={() => {}}
      onSaved={onSaved}
    />,
  )

  await user.type(screen.getByLabelText('账户名称'), '张三')
  await user.type(screen.getByLabelText('手机号'), '13800000000')
  await user.type(screen.getByLabelText('密码'), 'known-password')
  await user.click(screen.getByRole('button', { name: '验证并保存' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('账户验证失败，请重试')
  expect(screen.queryByText('账户验证成功')).not.toBeInTheDocument()
  expect(onSaved).toHaveBeenCalledTimes(1)
  expect(onSaved).toHaveBeenLastCalledWith(
    expect.objectContaining({ id: 'a' }),
    expect.objectContaining({ persisted: true, verified: false }),
  )
})

test('an invalid verification response cannot mark an existing account verified', async () => {
  const user = userEvent.setup()
  const onSaved = vi.fn()
  verifyAccount.mockResolvedValue(null)

  render(
    <AccountDialog
      open
      account={{ id: 'a', name: '张三', username: '13800000000', has_secret: true }}
      onOpenChange={() => {}}
      onSaved={onSaved}
    />,
  )

  await user.click(screen.getByRole('button', { name: '验证账户' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('账户验证失败，请重试')
  expect(screen.queryByText('账户验证成功')).not.toBeInTheDocument()
  expect(onSaved).not.toHaveBeenCalled()
})
