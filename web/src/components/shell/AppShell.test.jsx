import { render, screen } from '@testing-library/react'
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
