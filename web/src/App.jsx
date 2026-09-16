import React, { useCallback, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { listAccounts } from './api/accounts'
import { listTasks } from './api/tasks'
import AccountDialog, { publicAccount } from './components/accounts/AccountDialog'
import AppShell from './components/shell/AppShell'
import LaunchPage from './pages/LaunchPage'
import OverviewPage from './pages/OverviewPage'
import SettingsPage from './pages/SettingsPage'
import TaskPage from './pages/TaskPage'
import usePolling from './hooks/usePolling'

function asArray(value, key) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.data)) return value.data
  if (key && Array.isArray(value?.[key])) return value[key]
  return []
}

function taskIdOf(value) {
  const source = value?.task ?? value
  return source?.id ?? source?.task_id ?? source?.taskId
}

function normalizedAccounts(value) {
  return asArray(value, 'accounts').map(publicAccount).filter((account) => account?.id)
}

function normalizedTasks(value) {
  return asArray(value, 'tasks').filter((task) => task && typeof task === 'object')
}

function mergeTask(current, incoming) {
  const id = taskIdOf(incoming)
  if (id == null) return current
  const nextTask = incoming?.task ?? incoming
  const index = current.findIndex((task) => String(taskIdOf(task)) === String(id))
  if (index < 0) return [...current, nextTask]
  return current.map((task, taskIndex) => (taskIndex === index ? { ...task, ...nextTask } : task))
}

function errorMessage(error) {
  const message = error?.message
  if (typeof message !== 'string' || !message.trim()) return '账户概览加载失败，请重试'
  return message
    .replace(/\b(?:bearer\s+)[^\s,;}]+/gi, 'Bearer [redacted]')
    .replace(/((?:password|passwd|pass|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|cookies|key))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,;&\s}]+)/gi, '$1=[redacted]')
}

function isAborted(error, signal) {
  return Boolean(signal?.aborted)
    || error?.name === 'AbortError'
    || error?.code === 'ERR_CANCELED'
}

function NotFoundPage() {
  return (
    <section className="mx-auto w-full max-w-4xl px-4 py-8 md:px-8" aria-labelledby="not-found-title">
      <h1 id="not-found-title" className="text-xl font-semibold tracking-tight">页面不存在</h1>
      <p className="mt-2 text-sm leading-5 text-label-secondary">请返回任务总览继续操作。</p>
      <Link
        to="/"
        className="touch-target touch-target-compact mt-5 inline-flex min-h-9 items-center rounded-md border border-separator bg-surface px-3 text-sm text-label-primary hover:bg-black/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
      >
        返回任务总览
      </Link>
    </section>
  )
}

function NewAccountPage({ onSaved }) {
  const navigate = useNavigate()

  const handleOpenChange = (open) => {
    if (!open) {
      // The empty overview normally redirects first-time visitors here. Mark
      // an intentional dismissal so navigating back does not immediately
      // reopen the same controlled dialog.
      navigate('/', { replace: true, state: { accountDialogDismissed: true } })
    }
  }

  const handleSaved = (saved, meta) => {
    onSaved?.(saved)
    // AccountDialog reports once after persistence and again after the
    // first verification.  Navigate only after verification so the dialog
    // remains mounted if the remote account check needs a retry.
    if (saved?.id && meta?.verified) {
      navigate(`/accounts/${encodeURIComponent(saved.id)}/launch`)
    }
  }

  return (
    <>
      <section className="mx-auto w-full max-w-4xl px-4 py-8 md:px-8" aria-labelledby="new-account-title">
        <h1 id="new-account-title" className="text-xl font-semibold tracking-tight">添加账户</h1>
        <p className="mt-2 text-sm text-label-secondary">保存账户后即可选择课程并启动任务。</p>
      </section>
      <AccountDialog open onOpenChange={handleOpenChange} onSaved={handleSaved} />
    </>
  )
}

function RouteTree({
  accounts,
  tasks,
  loading,
  error,
  refresh,
  onAccountsChange,
  onTasksChange,
  onAccountSaved,
  onTaskCreated,
  onTaskSnapshot,
  taskRefreshError = false,
}) {
  const location = useLocation()
  const navigate = useNavigate()
  const hasAccounts = accounts.length > 0

  return (
    <Routes>
      <Route
        element={(
          <AppShell
            accounts={accounts}
            tasks={tasks}
            onAddAccount={() => navigate('/accounts/new')}
            onAccountSaved={onAccountSaved}
          />
        )}
      >
        <Route
          index
          element={(
            !loading
              && !error
              && !hasAccounts
              && location.pathname === '/'
              && !location.state?.accountDialogDismissed
              ? <Navigate to="/accounts/new" replace />
              : <OverviewPage
                accounts={accounts}
                tasks={tasks}
                loading={loading}
                error={error}
                onRefresh={refresh}
                refreshWarning={taskRefreshError}
                onAccountsChange={onAccountsChange}
                onTasksChange={onTasksChange}
                onAccountSaved={onAccountSaved}
              />
          )}
        />
        <Route path="accounts/new" element={<NewAccountPage onSaved={onAccountSaved} />} />
        <Route
          path="accounts/:accountId/launch"
          element={(
            <LaunchPage
              accounts={accounts}
              tasks={tasks}
              onTaskCreated={onTaskCreated}
            />
          )}
        />
        <Route
          path="tasks/:taskId"
          element={(
            <TaskPage
              accounts={accounts}
              tasks={tasks}
              onSnapshot={onTaskSnapshot}
            />
          )}
        />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

function AppContent() {
  const location = useLocation()
  const [taskRefreshError, setTaskRefreshError] = useState(false)
  const [accounts, setAccounts] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)
  const requestControllerRef = useRef(null)

  const refresh = useCallback(async () => {
    requestControllerRef.current?.abort()
    const controller = new AbortController()
    requestControllerRef.current = controller
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    try {
      const [accountResult, taskResult] = await Promise.all([
        listAccounts({ signal: controller.signal }),
        listTasks({ signal: controller.signal }),
      ])
      if (requestId !== requestIdRef.current) return
      setAccounts(normalizedAccounts(accountResult))
      setTasks(normalizedTasks(taskResult))
    } catch (requestError) {
      if (requestId !== requestIdRef.current) return
      if (isAborted(requestError, controller.signal)) return
      setError(errorMessage(requestError))
    } finally {
      if (requestControllerRef.current === controller) requestControllerRef.current = null
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    return () => {
      requestIdRef.current += 1
      requestControllerRef.current?.abort()
      requestControllerRef.current = null
    }
  }, [refresh])

  const pollTasks = useCallback((signal) => listTasks({ signal }), [])
  const handleTaskPoll = useCallback((result) => {
    setTasks(normalizedTasks(result))
    setTaskRefreshError(false)
  }, [])
  const handleTaskPollError = useCallback(() => setTaskRefreshError(true), [])
  usePolling(pollTasks, {
    enabled: !loading && !error && location.pathname === '/',
    intervalMs: 3000,
    onData: handleTaskPoll,
    onError: handleTaskPollError,
  })

  const handleAccountsChange = useCallback((next) => {
    setAccounts(normalizedAccounts(next))
  }, [])

  const handleTasksChange = useCallback((next) => {
    setTasks(normalizedTasks(next))
  }, [])

  const handleAccountSaved = useCallback((saved) => {
    const safeSaved = publicAccount(saved)
    if (!safeSaved?.id) return
    setAccounts((current) => {
      const index = current.findIndex((account) => String(account.id) === String(safeSaved.id))
      if (index < 0) return [...current, safeSaved]
      return current.map((account, accountIndex) => (
        accountIndex === index ? { ...account, ...safeSaved } : account
      ))
    })
  }, [])

  const handleTaskCreated = useCallback((result) => {
    const nextTask = result?.task ?? result
    setTasks((current) => mergeTask(current, nextTask))
  }, [])

  const handleTaskSnapshot = useCallback((snapshot) => {
    setTasks((current) => mergeTask(current, snapshot))
  }, [])

  return (
    <RouteTree
      accounts={accounts}
      tasks={tasks}
      loading={loading}
      error={error}
      refresh={refresh}
      onAccountsChange={handleAccountsChange}
      onTasksChange={handleTasksChange}
      onAccountSaved={handleAccountSaved}
      onTaskCreated={handleTaskCreated}
      onTaskSnapshot={handleTaskSnapshot}
      taskRefreshError={taskRefreshError}
    />
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  )
}

export { AppContent, NotFoundPage, RouteTree }
export default App
