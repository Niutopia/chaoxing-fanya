import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import AppShell from './AppShell'

test('shows saved accounts and active task state', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell
        accounts={[{ id: 'a', name: '张三', enabled: true }]}
        tasks={[{ id: 't', account_id: 'a', state: 'running' }]}
        onAddAccount={() => {}}
      />
    </MemoryRouter>,
  )
  expect(screen.getByText('张三')).toBeInTheDocument()
  expect(screen.getByLabelText('张三：运行中')).toBeInTheDocument()
})

test('marks shell controls as mobile-safe touch targets', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell
        accounts={[{ id: 'a', name: '张三', enabled: true }]}
        tasks={[]}
        onAddAccount={() => {}}
      />
    </MemoryRouter>,
  )

  expect(screen.getByRole('button', { name: '打开侧边栏' })).toHaveClass('touch-target')
  expect(screen.getByRole('link', { name: /张三/ })).toHaveClass('touch-target')
  expect(screen.getByRole('button', { name: '添加账户' })).toHaveClass('touch-target')
})

test('opens the account dialog when no external add handler is supplied', async () => {
  const user = userEvent.setup()

  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell accounts={[]} tasks={[]} />
    </MemoryRouter>,
  )

  await user.click(screen.getByRole('button', { name: '添加账户' }))

  expect(screen.getByRole('dialog', { name: '添加账户' })).toBeInTheDocument()
  expect(screen.getByLabelText('账户名称')).toHaveFocus()
})
