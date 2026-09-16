import React from 'react'
import { LayoutDashboard, Plus, Settings } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { cn } from '../../lib/utils'
import Button from '../ui/Button'
import StatusDot, { taskStateLabel } from '../ui/StatusDot'

const navLinkClass = ({ isActive }) =>
  cn(
    'touch-target touch-target-compact group flex min-h-9 items-center gap-2 rounded-md px-2.5 text-sm text-label-secondary transition-colors duration-150 hover:bg-black/[0.05] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue',
    isActive && 'bg-accent-blue/[0.1] font-medium text-accent-blue hover:bg-accent-blue/[0.14] hover:text-accent-blue',
  )

const ACTIVE_TASK_STATES = new Set(['running', 'stopping'])

function taskAccountId(task) {
  return task?.account_id ?? task?.accountId
}

function taskTimestamp(task) {
  const value = Number(task?.started_at ?? task?.startedAt ?? task?.finished_at ?? task?.finishedAt)
  return Number.isFinite(value) ? value : 0
}

function taskForAccount(tasks, accountId) {
  return (Array.isArray(tasks) ? tasks : [])
    .filter((task) => task && typeof task === 'object')
    .filter((task) => String(taskAccountId(task)) === String(accountId))
    .sort((left, right) => {
      const leftActive = ACTIVE_TASK_STATES.has(left?.state) ? 1 : 0
      const rightActive = ACTIVE_TASK_STATES.has(right?.state) ? 1 : 0
      return rightActive - leftActive || taskTimestamp(right) - taskTimestamp(left)
    })[0]
}

const AccountSidebar = React.forwardRef(function AccountSidebar(
  {
    accounts = [],
    tasks = [],
    onAddAccount,
    onNavigate,
    className,
  },
  ref,
) {
  return (
    <aside
      ref={ref}
      id="account-sidebar"
      aria-label="账户导航"
      className={cn(
        'shell-material w-full shrink-0 flex-col border-r border-separator md:flex md:h-full md:w-[220px]',
        className,
      )}
    >
      <nav aria-label="主要导航" className="space-y-0.5 p-3">
        <NavLink to="/" end className={navLinkClass} onClick={onNavigate}>
          <LayoutDashboard aria-hidden="true" size={16} strokeWidth={1.8} />
          <span>概览</span>
        </NavLink>
        <NavLink to="/settings" className={navLinkClass} onClick={onNavigate}>
          <Settings aria-hidden="true" size={16} strokeWidth={1.8} />
          <span>全局设置</span>
        </NavLink>
      </nav>

      <div className="flex min-h-0 flex-1 flex-col px-3 pb-3">
        <div className="flex items-center justify-between px-2.5 pb-2 pt-3">
          <h2 className="text-xs font-semibold text-label-tertiary">账户</h2>
          <span className="tabular-nums text-[11px] text-label-tertiary">{accounts.length}</span>
        </div>

        <nav aria-label="已保存账户" className="min-h-0 space-y-0.5 overflow-y-auto">
          {accounts.length === 0 ? (
            <p className="px-2.5 py-3 text-xs leading-5 text-label-secondary">还没有保存的账户</p>
          ) : (
            accounts.map((account) => {
              const task = taskForAccount(tasks, account.id)
              const state = account.enabled === false ? 'disabled' : task?.state ?? 'idle'
              const label = `${account.name}：${taskStateLabel(state)}`
              return (
                <NavLink
                  key={account.id}
                  to={`/accounts/${encodeURIComponent(account.id)}/launch`}
                  className={(linkState) =>
                    cn(navLinkClass(linkState), account.enabled === false && 'opacity-60')
                  }
                  onClick={onNavigate}
                >
                  <span className="min-w-0 flex-1 truncate">{account.name}</span>
                  <StatusDot status={state} label={label} />
                </NavLink>
              )
            })
          )}
        </nav>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-3 w-full justify-start gap-2 border-separator bg-transparent px-2.5 text-label-secondary hover:bg-black/[0.05] hover:text-label-primary"
          onClick={onAddAccount}
        >
          <Plus aria-hidden="true" size={15} strokeWidth={1.8} />
          <span>添加账户</span>
        </Button>
      </div>
    </aside>
  )
})

AccountSidebar.displayName = 'AccountSidebar'

export { AccountSidebar, taskForAccount }
export default AccountSidebar
