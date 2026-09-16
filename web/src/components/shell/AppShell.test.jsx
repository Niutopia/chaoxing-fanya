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
  expect(screen.getByText('超新星 · 学习助手')).toBeInTheDocument()
  expect(document.querySelector('img[src="/supernova.png"]')).toBeInTheDocument()
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

test('keeps the desktop shell bounded while the main region owns scrolling', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell accounts={[]} tasks={[]} onAddAccount={() => {}} />
    </MemoryRouter>,
  )

  const shell = document.querySelector('.app-shell')
  const middle = screen.getByRole('complementary', { name: '账户导航' }).parentElement
  const main = screen.getByRole('main', { name: '主要内容' })

  expect(shell).toHaveClass('md:h-dvh', 'md:overflow-hidden')
  expect(screen.getByRole('banner')).toHaveClass('shrink-0')
  expect(middle).toHaveClass('min-h-0')
  expect(screen.getByRole('complementary', { name: '账户导航' })).toHaveClass('md:h-full')
  expect(main).toHaveClass('md:h-full', 'md:overflow-y-auto', 'md:overscroll-contain')
  expect(main).toHaveAttribute('tabindex', '-1')
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
