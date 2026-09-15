import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import { RouteTree } from './App'

vi.mock('./api/accounts', () => ({
  createAccount: vi.fn(),
  deleteAccount: vi.fn(),
  listAccounts: vi.fn(),
  setAccountEnabled: vi.fn(),
  updateAccount: vi.fn(),
  verifyAccount: vi.fn(),
}))

vi.mock('./api/tasks', () => ({
  listTasks: vi.fn(),
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderEmptyAccountRoute(path = '/accounts/new') {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <RouteTree
        accounts={[]}
        tasks={[]}
        loading={false}
        error=""
        refresh={() => {}}
        onAccountsChange={() => {}}
        onTasksChange={() => {}}
        onAccountSaved={() => {}}
        onTaskCreated={() => {}}
        onTaskSnapshot={() => {}}
      />
      <LocationProbe />
    </MemoryRouter>,
  )
}

async function expectDismissedToEmptyOverview(action) {
  const user = userEvent.setup()
  renderEmptyAccountRoute()

  const dialog = await screen.findByRole('dialog', { name: '添加账户' })
  await action({ dialog, user })

  await waitFor(() => {
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/)
    expect(screen.queryByRole('dialog', { name: '添加账户' })).not.toBeInTheDocument()
  })
  expect(screen.getByRole('heading', { name: '账户概览' })).toBeInTheDocument()
}

test('the first empty overview visit still opens the new-account guide', async () => {
  renderEmptyAccountRoute('/')

  expect(await screen.findByRole('dialog', { name: '添加账户' })).toBeInTheDocument()
  expect(screen.getByTestId('location')).toHaveTextContent(/^\/accounts\/new$/)
})

test('closing the routed new-account dialog stays on the empty overview', async () => {
  await expectDismissedToEmptyOverview(async ({ user }) => {
    await user.click(screen.getByRole('button', { name: '关闭账户对话框' }))
  })
})

test('cancelling the routed new-account dialog stays on the empty overview', async () => {
  await expectDismissedToEmptyOverview(async ({ user }) => {
    await user.click(screen.getByRole('button', { name: '取消' }))
  })
})

test('Escape closes the routed new-account dialog without reopening it', async () => {
  await expectDismissedToEmptyOverview(async ({ user }) => {
    await user.keyboard('{Escape}')
  })
})

test('clicking the backdrop closes the routed new-account dialog without reopening it', async () => {
  await expectDismissedToEmptyOverview(async ({ dialog, user }) => {
    await user.click(dialog.previousElementSibling)
  })
})
