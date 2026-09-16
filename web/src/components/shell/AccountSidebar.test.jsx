import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import AccountSidebar from './AccountSidebar'

test('labels disabled accounts as disabled instead of idle', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AccountSidebar accounts={[{ id: 'a', name: '停用账号', enabled: false }]} tasks={[]} />
    </MemoryRouter>,
  )

  expect(screen.getByLabelText('停用账号：已停用')).toBeInTheDocument()
  expect(screen.getByLabelText('停用账号：已停用')).toHaveAttribute('data-status', 'disabled')
})

test('prefers an active task over a newer terminal task for the same account', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AccountSidebar
        accounts={[{ id: 'a', name: '学习账号', enabled: true }]}
        tasks={[
          { id: 'running', account_id: 'a', state: 'running', started_at: 10 },
          { id: 'latest', account_id: 'a', state: 'completed', started_at: 20 },
        ]}
      />
    </MemoryRouter>,
  )

  expect(screen.getByLabelText('学习账号：运行中')).toBeInTheDocument()
  expect(screen.queryByLabelText('学习账号：已完成')).not.toBeInTheDocument()
})

test('uses the latest task when no task for the account is active', () => {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AccountSidebar
        accounts={[{ id: 'a', name: '学习账号', enabled: true }]}
        tasks={[
          { id: 'old', account_id: 'a', state: 'failed', started_at: 10 },
          { id: 'latest', account_id: 'a', state: 'stopped', started_at: 20 },
        ]}
      />
    </MemoryRouter>,
  )

  expect(screen.getByLabelText('学习账号：已停止')).toBeInTheDocument()
  expect(screen.queryByLabelText('学习账号：失败')).not.toBeInTheDocument()
})
