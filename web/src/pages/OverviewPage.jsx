import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { MoreHorizontal, Plus, Trash2, X } from 'lucide-react'
import { deleteAccount, listAccounts, setAccountEnabled, verifyAccount } from '../api/accounts'
import { listTasks } from '../api/tasks'
import AccountDialog from '../components/accounts/AccountDialog'
import Alert from '../components/ui/Alert'
import Button from '../components/ui/Button'
import Progress from '../components/ui/Progress'
import StatusDot from '../components/ui/StatusDot'
import { cn } from '../lib/utils'

const TASK_LABELS = {
  idle: '空闲',
  running: '运行中',
  stopping: '正在停止',
  failed: '失败',
  completed: '已完成',
  stopped: '已停止',
}

const ACTIVE_TASK_STATES = new Set(['running', 'stopping'])

function accountIdOf(value) {
  return value?.account_id ?? value?.accountId
}

function taskTimestamp(task) {
  const value = Number(task?.started_at ?? task?.startedAt ?? task?.finished_at ?? task?.finishedAt)
  return Number.isFinite(value) ? value : 0
}

/** Choose the most useful snapshot when the API returns task history. */
function taskForAccount(tasks, accountId) {
  return tasks
    .filter((task) => String(accountIdOf(task)) === String(accountId))
    .sort((left, right) => {
      const leftActive = ACTIVE_TASK_STATES.has(left?.state) ? 1 : 0
      const rightActive = ACTIVE_TASK_STATES.has(right?.state) ? 1 : 0
      return rightActive - leftActive || taskTimestamp(right) - taskTimestamp(left)
    })[0]
}

function taskStateLabel(state) {
  return TASK_LABELS[state] ?? '未知状态'
}

/** Keep account identity useful at a glance without exposing a full login. */
function maskUsername(username) {
  const value = String(username ?? '').trim()
  if (!value) return '未设置'
  if (/^\d{11}$/.test(value)) {
    return `${value.slice(0, 3)}****${value.slice(-4)}`
  }
  if (value.length <= 2) return '••••'
  const visible = Math.max(1, Math.min(2, Math.floor(value.length / 4)))
  return `${value.slice(0, visible)}••••${value.slice(-visible)}`
}

function asArray(value, key) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.data)) return value.data
  if (key && Array.isArray(value?.[key])) return value[key]
  return []
}

function requestMessage(error, fallback) {
  const message = error?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

function accountField(account, snakeCase, camelCase = snakeCase) {
  return account?.[snakeCase] ?? account?.[camelCase]
}

const PUBLIC_ACCOUNT_FIELDS = [
  'id',
  'name',
  'username',
  'enabled',
  'has_secret',
  'has_cookies',
  'verification_status',
  'last_verified_at',
]

const PUBLIC_ACCOUNT_ALIASES = {
  has_secret: 'hasSecret',
  has_cookies: 'hasCookies',
  verification_status: 'verificationStatus',
  last_verified_at: 'lastVerifiedAt',
}

function publicAccount(value) {
  if (!value || typeof value !== 'object') return null
  return PUBLIC_ACCOUNT_FIELDS.reduce((result, field) => {
    const sourceField = value[field] !== undefined ? field : PUBLIC_ACCOUNT_ALIASES[field]
    if (sourceField && value[sourceField] !== undefined) result[field] = value[sourceField]
    return result
  }, {})
}

function mergedAccount(previous, next) {
  const safeNext = publicAccount(next)
  if (!safeNext) return previous
  return { ...previous, ...safeNext }
}

function AccountActionsMenu({ account, open, onToggle, onEdit, onVerify, onToggleEnabled, onDelete }) {
  const enabled = accountField(account, 'enabled') !== false

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        className="touch-target touch-target-compact inline-flex size-9 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
        aria-label={`${account.name}的更多操作`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onToggle}
      >
        <MoreHorizontal aria-hidden="true" size={17} strokeWidth={1.8} />
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={`${account.name}账户操作`}
          className="absolute right-0 top-10 z-20 min-w-40 rounded-md border border-separator bg-surface p-1"
        >
          <button
            type="button"
            role="menuitem"
            className="touch-target touch-target-compact flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-label-primary hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            onClick={onEdit}
          >
            编辑账户
          </button>
          <button
            type="button"
            role="menuitem"
            className="touch-target touch-target-compact flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-label-primary hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            onClick={onVerify}
          >
            重新验证
          </button>
          <button
            type="button"
            role="menuitem"
            className="touch-target touch-target-compact flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-label-primary hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            onClick={onToggleEnabled}
          >
            {enabled ? '停用账户' : '启用账户'}
          </button>
          <button
            type="button"
            role="menuitem"
            className="touch-target touch-target-compact flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-danger hover:bg-danger/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger"
            onClick={onDelete}
          >
            <Trash2 aria-hidden="true" size={15} strokeWidth={1.8} />
            删除账户
          </button>
        </div>
      ) : null}
    </div>
  )
}

function AccountRow({ account, task, menuOpen, onMenuToggle, onEdit, onVerify, onToggleEnabled, onDelete }) {
  const enabled = accountField(account, 'enabled') !== false
  const state = enabled ? task?.state ?? 'idle' : 'disabled'
  const label = enabled ? taskStateLabel(state) : '已停用'
  const currentCourse = task?.current_course ?? task?.currentCourse
  const currentChapter = task?.current_chapter ?? task?.currentChapter
  const progress = Number(task?.progress)
  const total = Number(task?.total)
  const safeProgress = Number.isFinite(progress) ? Math.max(progress, 0) : 0
  const safeTotal = Number.isFinite(total) && total > 0 ? total : 1
  const verificationStatus = accountField(account, 'verification_status', 'verificationStatus')
  const verificationLabel = {
    valid: '已验证',
    invalid: '验证失败',
    unverified: '未验证',
  }[verificationStatus] ?? ''
  const username = accountField(account, 'username')

  return (
    <div
      role="row"
      data-account-id={account.id}
      className={cn(
        'grid gap-4 border-t border-separator px-4 py-4 md:grid-cols-[minmax(180px,1.1fr)_minmax(180px,1fr)_minmax(180px,1.1fr)_auto] md:items-center',
        !enabled && 'bg-black/[0.025] text-label-secondary',
      )}
    >
      <div role="cell" className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <StatusDot
            status={enabled ? state : 'idle'}
            label={`${account.name}：${label}`}
          />
          <span className="min-w-0 truncate font-medium text-label-primary">{account.name}</span>
        </div>
        <p className="mt-1 truncate text-xs text-label-secondary">
          <span title="手机号已遮罩">{maskUsername(username)}</span>
          {verificationLabel ? <span className="ml-2">· {verificationLabel}</span> : null}
        </p>
      </div>

      <div role="cell" className="min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span className={cn('font-medium', !enabled && 'text-label-secondary')}>{label}</span>
          {task?.error ? (
            <span className="truncate text-xs text-danger" title={task.error}>
              {task.error}
            </span>
          ) : null}
        </div>
        <dl className="mt-1 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-xs leading-5 text-label-secondary">
          <dt>课程</dt>
          <dd className="truncate">{currentCourse || '—'}</dd>
          <dt>章节</dt>
          <dd className="truncate">{currentChapter || '—'}</dd>
        </dl>
      </div>

      <div role="cell" className="min-w-0">
        <Progress
          value={enabled ? safeProgress : 0}
          max={safeTotal}
          label={`${account.name}任务进度`}
          showValue
        />
      </div>

      <div role="cell" className="flex justify-end">
        <AccountActionsMenu
          account={account}
          open={menuOpen}
          onToggle={onMenuToggle}
          onEdit={onEdit}
          onVerify={onVerify}
          onToggleEnabled={onToggleEnabled}
          onDelete={onDelete}
        />
      </div>
    </div>
  )
}

function DeleteAccountDialog({ account, pending, error, onOpenChange, onConfirm }) {
  return (
    <Dialog.Root open={Boolean(account)} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(420px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-separator bg-surface p-5 text-label-primary outline-none"
          aria-describedby="delete-account-description"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold">确认删除账户</Dialog.Title>
              <Dialog.Description
                id="delete-account-description"
                className="mt-1 text-sm leading-5 text-label-secondary"
              >
                将删除“{account?.name}”及其保存的账户设置，此操作无法撤销。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="touch-target touch-target-compact inline-flex size-8 shrink-0 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                aria-label="关闭删除确认对话框"
              >
                <X aria-hidden="true" size={17} strokeWidth={1.8} />
              </button>
            </Dialog.Close>
          </div>

          {error ? (
            <Alert className="mt-4" variant="danger" aria-live="polite">
              {error}
            </Alert>
          ) : null}

          <div className="mt-5 flex justify-end gap-2 border-t border-separator pt-4">
            <Dialog.Close asChild>
              <Button type="button" variant="ghost">
                取消
              </Button>
            </Dialog.Close>
            <Button type="button" variant="destructive" loading={pending} onClick={onConfirm}>
              确认删除
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function OverviewPage() {
  const [accounts, setAccounts] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [menuAccountId, setMenuAccountId] = useState(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingAccount, setEditingAccount] = useState(null)
  const [confirmAccount, setConfirmAccount] = useState(null)
  const [confirmPending, setConfirmPending] = useState(false)
  const [confirmError, setConfirmError] = useState('')
  const [pendingAccountId, setPendingAccountId] = useState(null)
  const [actionMessage, setActionMessage] = useState('')
  const emptyActionRef = useRef(null)
  const requestIdRef = useRef(0)

  const loadData = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    try {
      const [accountResult, taskResult] = await Promise.all([listAccounts(), listTasks()])
      if (requestId !== requestIdRef.current) return
      setAccounts(asArray(accountResult, 'accounts').map(publicAccount).filter((account) => account?.id))
      setTasks(asArray(taskResult, 'tasks'))
    } catch (requestError) {
      if (requestId !== requestIdRef.current) return
      setError(requestMessage(requestError, '账户概览加载失败，请重试'))
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    return () => {
      requestIdRef.current += 1
    }
  }, [loadData])

  useEffect(() => {
    if (!loading && !error && accounts.length === 0) emptyActionRef.current?.focus()
  }, [accounts.length, error, loading])

  const openAddDialog = () => {
    setMenuAccountId(null)
    setEditingAccount(null)
    setActionMessage('')
    setDialogOpen(true)
  }

  const openEditDialog = (account) => {
    setMenuAccountId(null)
    setEditingAccount(account)
    setActionMessage('')
    setDialogOpen(true)
  }

  const handleDialogOpenChange = (nextOpen) => {
    setDialogOpen(nextOpen)
    if (!nextOpen) setEditingAccount(null)
  }

  const handleAccountSaved = (saved) => {
    const safeSaved = publicAccount(saved)
    if (!safeSaved?.id) return
    setAccounts((current) => {
      const index = current.findIndex((item) => String(item.id) === String(safeSaved.id))
      if (index < 0) return [...current, safeSaved]
      return current.map((item, itemIndex) => (itemIndex === index ? mergedAccount(item, safeSaved) : item))
    })
    setActionMessage('账户已更新')
  }

  const handleVerify = async (account) => {
    setMenuAccountId(null)
    setPendingAccountId(account.id)
    setActionMessage('')
    try {
      const verified = await verifyAccount(account.id)
      if (verified?.id) {
        setAccounts((current) => current.map((item) => (
          String(item.id) === String(verified.id) ? mergedAccount(item, verified) : item
        )))
      }
      setActionMessage('账户验证成功')
    } catch (requestError) {
      setActionMessage(requestMessage(requestError, '账户验证失败，请重试'))
    } finally {
      setPendingAccountId(null)
    }
  }

  const handleToggleEnabled = async (account) => {
    setMenuAccountId(null)
    setPendingAccountId(account.id)
    setActionMessage('')
    const nextEnabled = accountField(account, 'enabled') === false
    try {
      const updated = await setAccountEnabled(account.id, nextEnabled)
      setAccounts((current) => current.map((item) => (
        String(item.id) === String(account.id)
          ? mergedAccount(item, updated || { enabled: nextEnabled })
          : item
      )))
      setActionMessage(nextEnabled ? '账户已启用' : '账户已停用')
    } catch (requestError) {
      setActionMessage(requestMessage(requestError, '账户状态更新失败，请重试'))
    } finally {
      setPendingAccountId(null)
    }
  }

  const openDeleteDialog = (account) => {
    setMenuAccountId(null)
    setConfirmAccount(account)
    setConfirmError('')
  }

  const closeDeleteDialog = (nextOpen) => {
    if (!nextOpen && !confirmPending) {
      setConfirmAccount(null)
      setConfirmError('')
    }
  }

  const handleDelete = async () => {
    if (!confirmAccount || confirmPending) return
    setConfirmPending(true)
    setConfirmError('')
    try {
      await deleteAccount(confirmAccount.id)
      setAccounts((current) => current.filter((item) => String(item.id) !== String(confirmAccount.id)))
      setTasks((current) => current.filter((item) => String(accountIdOf(item)) !== String(confirmAccount.id)))
      setConfirmAccount(null)
      setActionMessage('账户已删除')
    } catch (requestError) {
      const code = requestError?.code
      if (code === 'account_active') {
        setConfirmError('请先停止该账户的任务')
      } else {
        setConfirmError(requestMessage(requestError, '删除账户失败，请重试'))
      }
    } finally {
      setConfirmPending(false)
    }
  }

  if (loading) {
    return (
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <h1 id="overview-title" className="text-xl font-semibold tracking-tight">账户概览</h1>
        <p className="mt-2 text-sm text-label-secondary" role="status" aria-live="polite">正在加载账户…</p>
      </section>
    )
  }

  if (error) {
    return (
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <h1 id="overview-title" className="text-xl font-semibold tracking-tight">账户概览</h1>
        <Alert className="mt-5 max-w-xl" variant="danger" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{error}</span>
            <Button type="button" variant="outline" onClick={loadData}>重试</Button>
          </div>
        </Alert>
      </section>
    )
  }

  return (
    <>
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 id="overview-title" className="text-xl font-semibold tracking-tight">账户概览</h1>
            <p className="mt-1 text-sm text-label-secondary">
              查看所有账户的任务状态与静态进度。
            </p>
          </div>
          <Button type="button" variant="outline" onClick={openAddDialog}>
            <Plus aria-hidden="true" size={15} strokeWidth={1.8} />
            添加账户
          </Button>
        </div>

        {actionMessage ? (
          <Alert className="mt-5" variant="info" aria-live="polite" onDismiss={() => setActionMessage('')}>
            {actionMessage}
          </Alert>
        ) : null}

        {accounts.length === 0 ? (
          <div className="mt-8 border-y border-separator bg-surface px-5 py-12 text-center">
            <h2 className="text-base font-semibold">还没有账户</h2>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-5 text-label-secondary">
              添加一个账户后，这里会显示其任务状态、课程和章节进度。
            </p>
            <Button ref={emptyActionRef} type="button" className="mt-5" onClick={openAddDialog}>
              添加第一个账户
            </Button>
          </div>
        ) : (
          <div className="mt-8 overflow-visible border-y border-separator bg-surface" role="table" aria-label="账户任务概览">
            <div role="row" className="hidden gap-4 px-4 py-2 text-xs font-medium text-label-tertiary md:grid md:grid-cols-[minmax(180px,1.1fr)_minmax(180px,1fr)_minmax(180px,1.1fr)_auto]">
              <div role="columnheader">账户</div>
              <div role="columnheader">任务状态</div>
              <div role="columnheader">进度</div>
              <div role="columnheader" aria-label="操作" />
            </div>
            {accounts.map((account) => (
              <AccountRow
                key={account.id}
                account={account}
                task={taskForAccount(tasks, account.id)}
                menuOpen={String(menuAccountId) === String(account.id)}
                onMenuToggle={() => setMenuAccountId((current) => (
                  String(current) === String(account.id) ? null : account.id
                ))}
                onEdit={() => openEditDialog(account)}
                onVerify={() => handleVerify(account)}
                onToggleEnabled={() => handleToggleEnabled(account)}
                onDelete={() => openDeleteDialog(account)}
              />
            ))}
          </div>
        )}
      </section>

      <AccountDialog
        open={dialogOpen}
        account={editingAccount}
        onOpenChange={handleDialogOpenChange}
        onSaved={handleAccountSaved}
      />

      <DeleteAccountDialog
        account={confirmAccount}
        pending={confirmPending}
        error={confirmError}
        onOpenChange={closeDeleteDialog}
        onConfirm={handleDelete}
      />

      {pendingAccountId ? (
        <span className="sr-only" role="status" aria-live="polite">
          正在更新账户
        </span>
      ) : null}
    </>
  )
}

export { AccountRow, AccountActionsMenu, maskUsername, taskForAccount }
export default OverviewPage
