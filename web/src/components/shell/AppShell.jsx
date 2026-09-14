import React, { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { cn } from '../../lib/utils'
import AccountDialog, { publicAccount } from '../accounts/AccountDialog'
import AccountSidebar from './AccountSidebar'
import Titlebar from './Titlebar'

function safeAccounts(values) {
  return (Array.isArray(values) ? values : [])
    .map(publicAccount)
    .filter((account) => account?.id)
}

function AppShell({ accounts = [], tasks = [], onAddAccount, onAccountSaved, className }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [localAccounts, setLocalAccounts] = useState(() => safeAccounts(accounts))
  const [accountDialogOpen, setAccountDialogOpen] = useState(false)

  useEffect(() => {
    setLocalAccounts(safeAccounts(accounts))
  }, [accounts])

  const closeSidebar = () => setSidebarOpen(false)
  const handleAddAccount = () => {
    if (typeof onAddAccount === 'function') {
      onAddAccount()
      return
    }
    setAccountDialogOpen(true)
  }

  const handleAccountSaved = (saved) => {
    const safeSaved = publicAccount(saved)
    if (safeSaved?.id) {
      setLocalAccounts((current) => {
        const index = current.findIndex((account) => String(account.id) === String(safeSaved.id))
        if (index < 0) return [...current, safeSaved]
        return current.map((account, accountIndex) => (
          accountIndex === index ? { ...account, ...safeSaved } : account
        ))
      })
    }
    onAccountSaved?.(safeSaved)
  }

  return (
    <>
      <div className={cn('app-shell flex min-h-dvh flex-col bg-canvas text-label-primary', className)}>
        <Titlebar
          sidebarOpen={sidebarOpen}
          onMenuToggle={() => setSidebarOpen((open) => !open)}
        />
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <AccountSidebar
            accounts={localAccounts}
            tasks={tasks}
            onAddAccount={handleAddAccount}
            onNavigate={closeSidebar}
            className={cn(!sidebarOpen && 'hidden md:flex')}
          />
          <main id="main-content" className="min-w-0 flex-1 bg-canvas" tabIndex="-1">
            <Outlet />
          </main>
        </div>
      </div>
      {typeof onAddAccount === 'function' ? null : (
        <AccountDialog
          open={accountDialogOpen}
          onOpenChange={setAccountDialogOpen}
          onSaved={handleAccountSaved}
        />
      )}
    </>
  )
}

export { AppShell }
export default AppShell
