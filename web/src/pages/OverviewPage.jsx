import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { MoreHorizontal, Trash2, X } from 'lucide-react'
import { Link } from 'react-router-dom'
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
  disabled: '已停用',
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

function taskIdOf(value) {
  return value?.id ?? value?.task_id ?? value?.taskId
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

function isAborted(error, signal) {
  return Boolean(signal?.aborted)
    || error?.name === 'AbortError'
    || error?.code === 'ERR_CANCELED'
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
  'auth_mode',
]

const PUBLIC_ACCOUNT_ALIASES = {
  has_secret: 'hasSecret',
  has_cookies: 'hasCookies',
  verification_status: 'verificationStatus',
  last_verified_at: 'lastVerifiedAt',
  auth_mode: 'authMode',
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

function AccountActionsMenu({ account, open, pending = false, onToggle, onEdit, onVerify, onToggleEnabled, onDelete }) {
  const enabled = accountField(account, 'enabled') !== false
  const triggerRef = useRef(null)
  const itemRefs = useRef([])
  const restoreFocusRef = useRef(false)
  const hasOpenedRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (open) {
      hasOpenedRef.current = true
      restoreFocusRef.current = true
      const focusTimer = window.setTimeout(() => {
        if (mountedRef.current && itemRefs.current[0]) itemRefs.current[0].focus()
      }, 0)
      return () => window.clearTimeout(focusTimer)
    }
    if (hasOpenedRef.current && restoreFocusRef.current && mountedRef.current) {
      triggerRef.current?.focus()
      const focusTimer = window.setTimeout(() => {
        if (mountedRef.current) triggerRef.current?.focus()
      }, 0)
      return () => window.clearTimeout(focusTimer)
    }
    return undefined
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const handleOutsidePointer = (event) => {
      if (!event.target.closest?.(`[data-account-menu="${account.id}"]`)) {
        closeMenu(true)
      }
    }
    document.addEventListener('pointerdown', handleOutsidePointer)
    return () => document.removeEventListener('pointerdown', handleOutsidePointer)
  }, [account.id, onToggle, open])

  const closeMenu = (restoreFocus) => {
    restoreFocusRef.current = restoreFocus
    if (restoreFocus && mountedRef.current) triggerRef.current?.focus()
    onToggle()
  }

  const handleMenuKeyDown = (event) => {
    const items = itemRefs.current.filter(Boolean)
    const currentIndex = items.indexOf(document.activeElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      closeMenu(true)
      return
    }
    if (event.key === 'Tab') {
      // Let the browser move focus naturally; closing the menu must not trap
      // keyboard users inside a transient action list.
      closeMenu(false)
      return
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || items.length === 0) return
    event.preventDefault()
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : (currentIndex + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
    items[nextIndex]?.focus()
  }

  const handleMenuBlur = (event) => {
    const nextTarget = event.relatedTarget
    if (!nextTarget?.closest?.(`[data-account-menu="${account.id}"]`)) {
      // A focusout with a concrete next target (for example Tab) should let
      // focus continue naturally; a pointer dismissal with no next target
      // can safely restore the menu trigger.
      const restoreFocus = nextTarget == null && restoreFocusRef.current
      restoreFocusRef.current = restoreFocus
      if (mountedRef.current && restoreFocus) triggerRef.current?.focus()
      onToggle()
    }
  }

  const runAction = (action, { restoreFocus = false } = {}) => {
    if (pending) return
    closeMenu(restoreFocus)
    action()
  }

  const menuItems = [
    { label: '编辑账户', action: onEdit },
    { label: '重新验证', action: onVerify, restoreFocus: true },
    { label: enabled ? '停用账户' : '启用账户', action: onToggleEnabled, restoreFocus: true },
    { label: '删除账户', action: onDelete, danger: true },
  ]

  return (
    <div className="relative shrink-0" data-account-menu={account.id}>
      <button
        ref={triggerRef}
        type="button"
        className="touch-target touch-target-compact inline-flex size-9 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
        aria-label={`${account.name}的更多操作`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={pending}
        onClick={onToggle}
      >
        <MoreHorizontal aria-hidden="true" size={17} strokeWidth={1.8} />
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={`${account.name}账户操作`}
          onKeyDown={handleMenuKeyDown}
          onBlur={handleMenuBlur}
          className="absolute right-0 top-10 z-20 min-w-40 rounded-md border border-separator bg-surface p-1"
        >
          {menuItems.map((item, index) => (
            <button
              key={item.label}
              ref={(element) => { itemRefs.current[index] = element }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={pending}
              className={cn(
                'touch-target touch-target-compact flex min-h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue',
                item.danger ? 'text-danger hover:bg-danger/[0.06] focus-visible:ring-danger' : 'text-label-primary',
              )}
              onClick={() => runAction(item.action, { restoreFocus: item.restoreFocus })}
            >
              {item.danger ? <Trash2 aria-hidden="true" size={15} strokeWidth={1.8} /> : null}
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function AccountRow({ account, task, actionError, menuOpen, pending, onMenuToggle, onEdit, onVerify, onToggleEnabled, onDelete }) {
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
  const taskId = taskIdOf(task)

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
            status={state}
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
        <div className="flex items-center gap-2">
          {enabled && taskId != null && ACTIVE_TASK_STATES.has(task?.state) ? (
            <Link
              to={`/tasks/${encodeURIComponent(String(taskId))}`}
              className="touch-target touch-target-compact inline-flex min-h-8 items-center rounded-md px-2.5 text-sm font-medium text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              查看任务
            </Link>
          ) : enabled && taskId != null && ['failed', 'stopped', 'completed'].includes(task?.state) ? (
            <Link
              to={`/tasks/${encodeURIComponent(String(taskId))}`}
              className="touch-target touch-target-compact inline-flex min-h-8 items-center rounded-md px-2.5 text-sm font-medium text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              {task?.state === 'failed' ? '查看错误' : '查看结果'}
            </Link>
          ) : enabled ? (
            <Link
              to={`/accounts/${encodeURIComponent(account.id)}/launch`}
              className="touch-target touch-target-compact inline-flex min-h-8 items-center rounded-md px-2.5 text-sm font-medium text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              配置并开始
            </Link>
          ) : null}
          <AccountActionsMenu
            account={account}
            open={menuOpen}
            pending={pending}
            onToggle={onMenuToggle}
            onEdit={onEdit}
            onVerify={onVerify}
            onToggleEnabled={onToggleEnabled}
            onDelete={onDelete}
          />
        </div>
      </div>
      {actionError ? (
        <div className="md:col-span-4">
          <Alert variant="danger" aria-live="polite">{actionError}</Alert>
        </div>
      ) : null}
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
          onEscapeKeyDown={(event) => {
            if (pending) event.preventDefault()
          }}
          onPointerDownOutside={(event) => {
            if (pending) event.preventDefault()
          }}
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
                disabled={pending}
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
              <Button type="button" variant="ghost" disabled={pending}>
                取消
              </Button>
            </Dialog.Close>
            <Button type="button" variant="destructive" disabled={pending} loading={pending} onClick={onConfirm}>
              确认删除
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function OverviewPage({
  accounts: suppliedAccounts,
  tasks: suppliedTasks,
  loading: suppliedLoading,
  error: suppliedError,
  onRefresh,
  onAccountsChange,
  onTasksChange,
  onAccountSaved: reportAccountSaved,
}) {
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
  const [pendingAccountIds, setPendingAccountIds] = useState({})
  const [pendingAccountActions, setPendingAccountActions] = useState({})
  const [actionMessage, setActionMessage] = useState('')
  const [actionErrors, setActionErrors] = useState({})
  const requestIdRef = useRef(0)
  const loadControllerRef = useRef(null)
  const pendingAccountIdsRef = useRef(new Set())
  const mountedRef = useRef(false)
  const accountOperationRef = useRef(new Map())
  const accountGenerationRef = useRef(new Map())
  const deleteOperationRef = useRef(null)
  const deleteGenerationRef = useRef(0)
  const controlledData = suppliedAccounts !== undefined
    || suppliedTasks !== undefined
    || suppliedLoading !== undefined
    || suppliedError !== undefined

  const visibleAccounts = suppliedAccounts !== undefined
    ? (Array.isArray(suppliedAccounts) ? suppliedAccounts.map(publicAccount).filter((account) => account?.id) : [])
    : accounts
  const visibleTasks = suppliedTasks !== undefined
    ? (Array.isArray(suppliedTasks) ? suppliedTasks : [])
    : tasks
  const visibleLoading = suppliedLoading !== undefined ? Boolean(suppliedLoading) : loading
  const visibleError = suppliedError !== undefined ? String(suppliedError || '') : error

  const loadData = useCallback(async () => {
    loadControllerRef.current?.abort()
    const controller = new AbortController()
    loadControllerRef.current = controller
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    try {
      const [accountResult, taskResult] = await Promise.all([
        listAccounts({ signal: controller.signal }),
        listTasks({ signal: controller.signal }),
      ])
      if (!mountedRef.current || controller.signal.aborted || requestId !== requestIdRef.current) return
      setAccounts(asArray(accountResult, 'accounts').map(publicAccount).filter((account) => account?.id))
      setTasks(asArray(taskResult, 'tasks'))
    } catch (requestError) {
      if (!mountedRef.current || requestId !== requestIdRef.current || isAborted(requestError, controller.signal)) return
      setError(requestMessage(requestError, '账户概览加载失败，请重试'))
    } finally {
      if (loadControllerRef.current === controller) loadControllerRef.current = null
      if (mountedRef.current && !controller.signal.aborted && requestId === requestIdRef.current) setLoading(false)
    }
  }, [])

  const refreshData = typeof onRefresh === 'function' ? onRefresh : loadData

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestIdRef.current += 1
      loadControllerRef.current?.abort()
      loadControllerRef.current = null
      accountOperationRef.current.forEach((operation) => operation.controller.abort())
      accountOperationRef.current.clear()
      deleteOperationRef.current?.controller.abort()
      deleteOperationRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!controlledData) loadData()
    return () => {
      requestIdRef.current += 1
      loadControllerRef.current?.abort()
      loadControllerRef.current = null
    }
  }, [controlledData, loadData])

  useEffect(() => {
    if (suppliedAccounts !== undefined) {
      setAccounts(Array.isArray(suppliedAccounts) ? suppliedAccounts.map(publicAccount).filter((account) => account?.id) : [])
    }
    if (suppliedTasks !== undefined) setTasks(Array.isArray(suppliedTasks) ? suppliedTasks : [])
  }, [suppliedAccounts, suppliedTasks])

  const openEditDialog = (account) => {
    setMenuAccountId(null)
    setEditingAccount(account)
    setActionMessage('')
    setActionErrors({})
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
      const next = index < 0
        ? [...current, safeSaved]
        : current.map((item, itemIndex) => (itemIndex === index ? mergedAccount(item, safeSaved) : item))
      onAccountsChange?.(next)
      return next
    })
    reportAccountSaved?.(safeSaved)
    setActionMessage('账户已更新')
    setActionErrors((current) => {
      const next = { ...current }
      delete next[String(safeSaved.id)]
      return next
    })
  }

  const beginAccountAction = (accountId, accountName, action) => {
    const key = String(accountId)
    if (!mountedRef.current || pendingAccountIdsRef.current.has(key)) return null
    const generation = (accountGenerationRef.current.get(key) ?? 0) + 1
    const operation = {
      accountId,
      controller: new AbortController(),
      generation,
      key,
    }
    accountGenerationRef.current.set(key, generation)
    accountOperationRef.current.set(key, operation)
    pendingAccountIdsRef.current.add(key)
    setPendingAccountIds((current) => ({ ...current, [key]: true }))
    setPendingAccountActions((current) => ({
      ...current,
      [key]: { accountName: String(accountName ?? '未命名账户'), action },
    }))
    return operation
  }

  const isMountedCurrentAccountAction = (operation) => (
    Boolean(operation)
    && mountedRef.current
    && accountOperationRef.current.get(operation.key) === operation
    && accountGenerationRef.current.get(operation.key) === operation.generation
  )

  const isCurrentAccountAction = (operation) => (
    isMountedCurrentAccountAction(operation)
    && !operation.controller.signal.aborted
  )

  const endAccountAction = (operation) => {
    if (!isMountedCurrentAccountAction(operation)) return
    const { key } = operation
    accountOperationRef.current.delete(key)
    pendingAccountIdsRef.current.delete(key)
    setPendingAccountIds((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setPendingAccountActions((current) => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  const handleVerify = async (account) => {
    const operation = beginAccountAction(account.id, account.name, '验证')
    if (!operation) return
    setMenuAccountId(null)
    setActionMessage('')
    setActionErrors((current) => ({ ...current, [String(account.id)]: '' }))
    try {
      const verified = await verifyAccount(account.id, { signal: operation.controller.signal })
      if (!isCurrentAccountAction(operation)) return
      const safeVerified = publicAccount(verified)
      if (safeVerified?.verification_status !== 'valid') {
        throw new Error('账户验证失败，请重试')
      }
      setAccounts((current) => {
        if (!isCurrentAccountAction(operation)) return current
        const targetId = safeVerified.id ?? account.id
        const next = current.map((item) => (
          String(item.id) === String(targetId) ? mergedAccount(item, safeVerified) : item
        ))
        onAccountsChange?.(next)
        return next
      })
      if (!isCurrentAccountAction(operation)) return
      setActionMessage('账户验证成功')
      setActionErrors((current) => ({ ...current, [String(account.id)]: '' }))
    } catch (requestError) {
      if (!isCurrentAccountAction(operation) || isAborted(requestError, operation.controller.signal)) return
      setActionErrors((current) => ({
        ...current,
        [String(account.id)]: requestMessage(requestError, '账户验证失败，请重试'),
      }))
    } finally {
      endAccountAction(operation)
    }
  }

  const handleToggleEnabled = async (account) => {
    const nextEnabled = accountField(account, 'enabled') === false
    const operation = beginAccountAction(account.id, account.name, nextEnabled ? '启用' : '停用')
    if (!operation) return
    setMenuAccountId(null)
    setActionMessage('')
    setActionErrors((current) => ({ ...current, [String(account.id)]: '' }))
    try {
      const updated = await setAccountEnabled(account.id, nextEnabled, { signal: operation.controller.signal })
      if (!isCurrentAccountAction(operation)) return
      setAccounts((current) => {
        if (!isCurrentAccountAction(operation)) return current
        const next = current.map((item) => (
          String(item.id) === String(account.id)
            ? mergedAccount(item, updated || { enabled: nextEnabled })
            : item
        ))
        onAccountsChange?.(next)
        return next
      })
      if (!isCurrentAccountAction(operation)) return
      setActionMessage(nextEnabled ? '账户已启用' : '账户已停用')
      setActionErrors((current) => ({ ...current, [String(account.id)]: '' }))
    } catch (requestError) {
      if (!isCurrentAccountAction(operation) || isAborted(requestError, operation.controller.signal)) return
      setActionErrors((current) => ({
        ...current,
        [String(account.id)]: requestMessage(requestError, '账户状态更新失败，请重试'),
      }))
    } finally {
      endAccountAction(operation)
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
    const account = confirmAccount
    if (!account || deleteOperationRef.current || !mountedRef.current) return
    const operation = {
      accountId: account.id,
      controller: new AbortController(),
      generation: deleteGenerationRef.current + 1,
    }
    deleteGenerationRef.current = operation.generation
    deleteOperationRef.current = operation
    setConfirmPending(true)
    setConfirmError('')
    try {
      await deleteAccount(account.id, { signal: operation.controller.signal })
      if (!isCurrentDeleteOperation(operation)) return
      setAccounts((current) => {
        if (!isCurrentDeleteOperation(operation)) return current
        const next = current.filter((item) => String(item.id) !== String(account.id))
        onAccountsChange?.(next)
        return next
      })
      if (!isCurrentDeleteOperation(operation)) return
      setTasks((current) => {
        if (!isCurrentDeleteOperation(operation)) return current
        const next = current.filter((item) => String(accountIdOf(item)) !== String(account.id))
        onTasksChange?.(next)
        return next
      })
      if (!isCurrentDeleteOperation(operation)) return
      setConfirmAccount(null)
      setActionMessage('账户已删除')
    } catch (requestError) {
      if (!isCurrentDeleteOperation(operation) || isAborted(requestError, operation.controller.signal)) return
      const code = requestError?.code
      if (code === 'account_active') {
        setConfirmError('请先停止该账户的任务')
      } else {
        setConfirmError(requestMessage(requestError, '删除账户失败，请重试'))
      }
    } finally {
      endDeleteOperation(operation)
    }
  }

  const isMountedCurrentDeleteOperation = (operation) => (
    Boolean(operation)
    && mountedRef.current
    && deleteOperationRef.current === operation
    && deleteGenerationRef.current === operation.generation
  )

  const isCurrentDeleteOperation = (operation) => (
    isMountedCurrentDeleteOperation(operation)
    && !operation.controller.signal.aborted
  )

  const endDeleteOperation = (operation) => {
    if (!isMountedCurrentDeleteOperation(operation)) return
    deleteOperationRef.current = null
    setConfirmPending(false)
  }

  if (visibleLoading) {
    return (
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <h1 id="overview-title" className="text-xl font-semibold tracking-tight">账户概览</h1>
        <p className="mt-2 text-sm text-label-secondary" role="status" aria-live="polite">正在加载账户…</p>
      </section>
    )
  }

  if (visibleError) {
    return (
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <h1 id="overview-title" className="text-xl font-semibold tracking-tight">账户概览</h1>
        <Alert className="mt-5 max-w-xl" variant="danger" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{visibleError}</span>
            <Button type="button" variant="outline" onClick={refreshData}>重试</Button>
          </div>
        </Alert>
      </section>
    )
  }

  return (
    <>
      <section className="mx-auto w-full max-w-6xl px-4 py-8 md:px-8" aria-labelledby="overview-title">
        <div>
          <div>
            <h1 id="overview-title" className="text-balance text-xl font-semibold">账户概览</h1>
            <p className="mt-1 text-pretty text-sm text-label-secondary">
              查看所有账户的任务状态与静态进度。
            </p>
          </div>
        </div>

        {actionMessage ? (
          <Alert className="mt-5" variant="info" aria-live="polite" onDismiss={() => setActionMessage('')}>
            {actionMessage}
          </Alert>
        ) : null}

        {visibleAccounts.length === 0 ? (
          <div className="mt-8 border-y border-separator bg-surface px-5 py-12 text-center">
            <h2 className="text-base font-semibold">还没有账户</h2>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-5 text-label-secondary">
              添加一个账户后，这里会显示其任务状态、课程和章节进度。
            </p>
            <p className="mx-auto mt-4 max-w-sm text-pretty text-xs leading-5 text-label-tertiary">
              请使用侧栏底部的“添加账户”。
            </p>
          </div>
        ) : (
          <div className="mt-8 overflow-visible border-y border-separator bg-surface" role="table" aria-label="账户任务概览">
            <div role="row" className="hidden gap-4 px-4 py-2 text-xs font-medium text-label-tertiary md:grid md:grid-cols-[minmax(180px,1.1fr)_minmax(180px,1fr)_minmax(180px,1.1fr)_auto]">
              <div role="columnheader">账户</div>
              <div role="columnheader">任务状态</div>
              <div role="columnheader">进度</div>
              <div role="columnheader" aria-label="操作" />
            </div>
            {visibleAccounts.map((account) => (
              <AccountRow
                key={account.id}
                account={account}
                task={taskForAccount(visibleTasks, account.id)}
                actionError={actionErrors[String(account.id)]}
                menuOpen={String(menuAccountId) === String(account.id)}
                pending={Boolean(pendingAccountIds[String(account.id)])}
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

      {Object.keys(pendingAccountActions).length > 0 || confirmPending ? (
        <span className="sr-only" role="status" aria-live="polite" data-testid="overview-pending-status">
          {[
            ...Object.values(pendingAccountActions).map(({ accountName, action }) => `正在${action}账户“${accountName}”…`),
            ...(confirmPending && confirmAccount ? [`正在删除账户“${confirmAccount.name}”…`] : []),
          ].join('；')}
        </span>
      ) : null}
    </>
  )
}

export { AccountRow, AccountActionsMenu, maskUsername, taskForAccount, taskIdOf }
export default OverviewPage
