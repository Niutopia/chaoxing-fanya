import React, { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { cn } from '../../lib/utils'
import AccountSidebar from './AccountSidebar'
import Titlebar from './Titlebar'

function AppShell({ accounts = [], tasks = [], onAddAccount = () => {}, className }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const closeSidebar = () => setSidebarOpen(false)

  return (
    <div className={cn('app-shell flex min-h-dvh flex-col bg-canvas text-label-primary', className)}>
      <Titlebar
        sidebarOpen={sidebarOpen}
        onMenuToggle={() => setSidebarOpen((open) => !open)}
      />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <AccountSidebar
          accounts={accounts}
          tasks={tasks}
          onAddAccount={onAddAccount}
          onNavigate={closeSidebar}
          className={cn(!sidebarOpen && 'hidden md:flex')}
        />
        <main id="main-content" className="min-w-0 flex-1 bg-canvas" tabIndex="-1">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export { AppShell }
export default AppShell
