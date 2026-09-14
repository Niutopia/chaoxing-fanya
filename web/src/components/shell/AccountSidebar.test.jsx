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
