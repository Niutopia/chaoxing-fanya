import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getAnswerConnection } from '../api/settings'
import { getPreferences, listCourses, savePreferences } from '../api/accounts'
import { startTask } from '../api/tasks'
import Alert from '../components/ui/Alert'
import Button from '../components/ui/Button'
import Field from '../components/ui/Field'
import Input from '../components/ui/Input'
import { cn } from '../lib/utils'

const DEFAULT_PREFERENCES = {
  selected_course_ids: [],
  speed: 1,
  jobs: 4,
  notopen_action: 'retry',
  answer_enabled: false,
  answer_cover_rate: 0.9,
  answer_auto_submit: false,
}

const ACTIVE_TASK_STATES = new Set(['running', 'stopping'])

function unwrap(value, keys = []) {
  if (value && typeof value === 'object') {
    for (const key of keys) {
      if (value[key] !== undefined) return value[key]
    }
    if (value.data !== undefined) return value.data
  }
  return value
}

function asCourses(value) {
  const list = unwrap(value, ['courses'])
  if (!Array.isArray(list)) return []
  return list
    .map((course) => {
      if (typeof course === 'string') return { courseId: course, title: course }
      if (!course || typeof course !== 'object') return null
      const courseId = course.courseId ?? course.course_id ?? course.id
      const title = course.title ?? course.name ?? course.courseName ?? course.course_name
      if (courseId == null || title == null) return null
      return {
        ...course,
        courseId: String(courseId),
        title: String(title),
      }
    })
    .filter(Boolean)
}

function asPreferences(value) {
  const source = unwrap(value, ['preferences'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { ...DEFAULT_PREFERENCES }
  }

  return {
    ...DEFAULT_PREFERENCES,
    selected_course_ids: Array.isArray(source.selected_course_ids)
      ? source.selected_course_ids.map(String)
      : DEFAULT_PREFERENCES.selected_course_ids,
    speed: source.speed ?? DEFAULT_PREFERENCES.speed,
    jobs: source.jobs ?? DEFAULT_PREFERENCES.jobs,
    notopen_action: source.notopen_action ?? DEFAULT_PREFERENCES.notopen_action,
    answer_enabled: source.answer_enabled ?? DEFAULT_PREFERENCES.answer_enabled,
    answer_cover_rate: source.answer_cover_rate ?? DEFAULT_PREFERENCES.answer_cover_rate,
    answer_auto_submit: source.answer_auto_submit ?? DEFAULT_PREFERENCES.answer_auto_submit,
  }
}

function accountValue(account, snakeCase, camelCase = snakeCase) {
  return account?.[snakeCase] ?? account?.[camelCase]
}

function accountIsEnabled(account) {
  return account ? accountValue(account, 'enabled') !== false : true
}

function activeTaskFor(accountId, task, tasks) {
  const candidate = task || (Array.isArray(tasks)
    ? tasks.find((item) => String(item?.account_id ?? item?.accountId) === String(accountId))
    : null)
  return candidate && ACTIVE_TASK_STATES.has(candidate.state) ? candidate : null
}

function errorMessage(error, fallback) {
  const message = error?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

function isAborted(error, signal) {
  return Boolean(signal?.aborted)
    || error?.name === 'AbortError'
    || error?.code === 'ERR_CANCELED'
}

function startErrorMessage(error) {
  switch (error?.code) {
    case 'account_active':
      return '该账户已有任务在运行'
    case 'account_disabled':
      return '请先启用该账户'
    case 'courses_required':
      return '请至少选择一门课程'
    case 'invalid_preferences':
      return '请检查学习参数'
    case 'answer_not_ready':
    case 'answer_not_tested':
      return '请先在设置中测试答题连接'
    case 'task_limit_reached':
      return '已达到同时运行账户上限，请稍后再试'
    default:
      return errorMessage(error, '启动学习任务失败，请重试')
  }
}

function numericValue(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function preferencePayload(preferences) {
  return {
    selected_course_ids: [...preferences.selected_course_ids],
    speed: numericValue(preferences.speed, DEFAULT_PREFERENCES.speed),
    jobs: Math.trunc(numericValue(preferences.jobs, DEFAULT_PREFERENCES.jobs)),
    notopen_action: preferences.notopen_action,
    answer_enabled: Boolean(preferences.answer_enabled),
    answer_cover_rate: numericValue(
      preferences.answer_cover_rate,
      DEFAULT_PREFERENCES.answer_cover_rate,
    ),
    answer_auto_submit: Boolean(preferences.answer_auto_submit),
  }
}

function taskIdFrom(value) {
  const result = unwrap(value, ['task'])
  if (typeof result === 'string' || typeof result === 'number') return String(result)
  if (!result || typeof result !== 'object') return ''
  const id = result.id ?? result.task_id ?? result.taskId
  return id == null ? '' : String(id)
}

function answerConnectionReady(connection) {
  if (!connection) return false
  if (connection.enabled !== true) return false
  if (connection.has_api_key !== true) return false
  const status = connection.last_test_status ?? connection.test_status ?? connection.testStatus
  // A task may only enable answering after the saved connection has a
  // successful probe.  Missing/unknown status is intentionally blocked: the
  // server cannot safely infer that a draft or an older response was tested.
  return status === 'success'
}

function preferenceNumber(value, fallback) {
  return value === undefined || value === null ? fallback : value
}

function LaunchPage({
  account,
  accounts = [],
  accountId: explicitAccountId,
  activeTask,
  task,
  tasks,
  onTaskCreated,
  className,
}) {
  const params = useParams()
  const navigate = useNavigate()
  const accountId = explicitAccountId ?? params.accountId ?? account?.id
  const activeAccount = account || accounts.find((item) => String(item?.id) === String(accountId))
  const [courses, setCourses] = useState([])
  const [preferences, setPreferences] = useState({ ...DEFAULT_PREFERENCES })
  const [answerConnection, setAnswerConnection] = useState(null)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [coursesLoading, setCoursesLoading] = useState(true)
  const [initialReady, setInitialReady] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [stale, setStale] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [answerError, setAnswerError] = useState('')
  const [actionError, setActionError] = useState('')
  const [saving, setSaving] = useState(false)
  const requestId = useRef(0)
  const hasCoursesRef = useRef(false)
  const accountGenerationRef = useRef(0)
  const accountIdRef = useRef(accountId)
  const startOperationRef = useRef(0)
  const loadControllerRef = useRef(null)
  const startControllerRef = useRef(null)

  // Keep the identity available to async callbacks during the render in
  // which the route/prop changes.  The effect below also clears all
  // account-scoped state before the new account's requests can complete.
  accountIdRef.current = accountId

  useEffect(() => {
    loadControllerRef.current?.abort()
    startControllerRef.current?.abort()
    loadControllerRef.current = null
    startControllerRef.current = null
    const previousGeneration = accountGenerationRef.current
    accountGenerationRef.current = previousGeneration + 1
    requestId.current += 1
    startOperationRef.current += 1
    hasCoursesRef.current = false
    setCourses([])
    setPreferences({ ...DEFAULT_PREFERENCES })
    setAnswerConnection(null)
    setSearch('')
    setInitialReady(false)
    setLoading(Boolean(accountId))
    setCoursesLoading(Boolean(accountId))
    setRefreshing(false)
    setStale(false)
    setLoadError('')
    setAnswerError('')
    setActionError('')
    setSaving(false)
  }, [accountId])

  const loadPage = useCallback(async ({ refresh = false } = {}) => {
    if (!accountId) {
      setLoading(false)
      setCoursesLoading(false)
      setInitialReady(false)
      setLoadError('缺少账户信息')
      return
    }

    loadControllerRef.current?.abort()
    const controller = new AbortController()
    loadControllerRef.current = controller
    const currentRequest = ++requestId.current
    const currentGeneration = accountGenerationRef.current
    const isCurrentRequest = () => (
      currentRequest === requestId.current
      && loadControllerRef.current === controller
      && !controller.signal.aborted
      && currentGeneration === accountGenerationRef.current
      && String(accountIdRef.current ?? '') === String(accountId ?? '')
    )
    if (refresh) {
      setRefreshing(true)
    } else {
      setLoading(true)
      setCoursesLoading(true)
      setInitialReady(false)
      setLoadError('')
      setAnswerError('')
    }
    setActionError('')

    const coursePromise = refresh
      ? listCourses(accountId, { refresh: true, signal: controller.signal })
      : listCourses(accountId, { signal: controller.signal })
    const preferencesPromise = refresh ? null : getPreferences(accountId, { signal: controller.signal })
    const connectionPromise = refresh ? null : getAnswerConnection({ signal: controller.signal })

    let coursesSucceeded = false
    try {
      const courseResult = await coursePromise
      if (!isCurrentRequest()) return
      setCourses(asCourses(courseResult))
      coursesSucceeded = true
      hasCoursesRef.current = true
      setStale(false)
      setLoadError('')
    } catch (error) {
      if (!isCurrentRequest()) return
      if (isAborted(error, controller.signal)) return
      if (refresh && hasCoursesRef.current) {
        setStale(true)
      } else {
        setLoadError(errorMessage(error, '课程加载失败，请重试'))
      }
    } finally {
      if (isCurrentRequest()) {
        setCoursesLoading(false)
        setRefreshing(false)
      }
    }

    if (!refresh) {
      const [preferencesResult, connectionResult] = await Promise.allSettled([
        preferencesPromise,
        connectionPromise,
      ])
      if (!isCurrentRequest()) return

      if (preferencesResult.status === 'fulfilled') {
        setPreferences(asPreferences(preferencesResult.value))
      } else {
        setLoadError((current) => current || errorMessage(preferencesResult.reason, '学习参数加载失败，请重试'))
      }

      if (connectionResult.status === 'fulfilled') {
        setAnswerConnection(unwrap(connectionResult.value, ['connection', 'answer_connection']))
        setAnswerError('')
      } else {
        setAnswerError(errorMessage(connectionResult.reason, '答题连接状态加载失败'))
      }
      setInitialReady(
        coursesSucceeded
        && preferencesResult.status === 'fulfilled'
        && connectionResult.status === 'fulfilled',
      )
      setLoading(false)
    }
    if (loadControllerRef.current === controller) loadControllerRef.current = null
  }, [accountId])

  useEffect(() => {
    loadPage()
    return () => {
      requestId.current += 1
      loadControllerRef.current?.abort()
      startControllerRef.current?.abort()
      loadControllerRef.current = null
      startControllerRef.current = null
    }
  }, [loadPage])

  const filteredCourses = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    if (!needle) return courses
    return courses.filter((course) => (
      course.title.toLocaleLowerCase().includes(needle)
      || course.courseId.toLocaleLowerCase().includes(needle)
    ))
  }, [courses, search])

  const selectedIds = Array.isArray(preferences.selected_course_ids)
    ? preferences.selected_course_ids.map(String)
    : []
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds.join('|')])
  const selectedCount = selectedIds.length
  const enabled = accountIsEnabled(activeAccount)
  const runningTask = activeTaskFor(accountId, activeTask || task, tasks)
  const connectionBlocked = Boolean(preferences.answer_enabled)
    && (answerError || !answerConnectionReady(answerConnection))
  const launchControlsDisabled = !initialReady || saving

  const updatePreference = (field) => (event) => {
    const nextValue = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setPreferences((current) => ({ ...current, [field]: nextValue }))
    setActionError('')
  }

  const toggleCourse = (courseId) => {
    setPreferences((current) => {
      const currentIds = Array.isArray(current.selected_course_ids)
        ? current.selected_course_ids.map(String)
        : []
      const next = currentIds.includes(String(courseId))
        ? currentIds.filter((id) => id !== String(courseId))
        : [...currentIds, String(courseId)]
      return { ...current, selected_course_ids: next }
    })
    setActionError('')
  }

  const selectAll = () => {
    const visibleIds = filteredCourses.map((course) => course.courseId)
    setPreferences((current) => {
      const existing = Array.isArray(current.selected_course_ids)
        ? current.selected_course_ids.map(String)
        : []
      return {
        ...current,
        selected_course_ids: [...new Set([...existing, ...visibleIds])],
      }
    })
    setActionError('')
  }

  const clearSelection = () => {
    setPreferences((current) => ({ ...current, selected_course_ids: [] }))
    setActionError('')
  }

  const validate = () => {
    if (!initialReady) return '请等待课程、学习参数和连接加载完成'
    if (!enabled) return '请先启用该账户'
    if (runningTask) return '该账户已有任务在运行'
    if (selectedIds.length === 0) return '请至少选择一门课程'

    const speed = numericValue(preferences.speed, Number.NaN)
    if (!Number.isFinite(speed) || speed < 1 || speed > 2) return '播放速度需在 1 到 2 之间'
    if (!/^\d+$/.test(String(preferences.jobs)) || Number(preferences.jobs) < 1 || Number(preferences.jobs) > 10) {
      return '章节并发数需为 1 到 10 的整数'
    }
    const coverRate = numericValue(preferences.answer_cover_rate, Number.NaN)
    if (!Number.isFinite(coverRate) || coverRate < 0 || coverRate > 1) return '答题覆盖率需在 0 到 1 之间'
    if (connectionBlocked) return '请先在设置中测试答题连接'
    return ''
  }

  const handleStart = async () => {
    if (saving) return
    const validationError = validate()
    if (validationError) {
      setActionError(validationError)
      return
    }

    const requestedAccountId = accountId
    const requestedGeneration = accountGenerationRef.current
    const operation = ++startOperationRef.current
    startControllerRef.current?.abort()
    const controller = new AbortController()
    startControllerRef.current = controller
    const isCurrentStart = () => (
      operation === startOperationRef.current
      && startControllerRef.current === controller
      && !controller.signal.aborted
      && requestedGeneration === accountGenerationRef.current
      && String(accountIdRef.current ?? '') === String(requestedAccountId ?? '')
    )
    setSaving(true)
    setActionError('')
    const payload = preferencePayload(preferences)
    try {
      await savePreferences(accountId, payload, { signal: controller.signal })
      // Account switching while the preference write is in flight must not
      // leak this account's successful save into another account's task.
      if (!isCurrentStart()) return
      const result = await startTask(
        accountId,
        { course_ids: [...selectedIds] },
        { signal: controller.signal },
      )
      if (!isCurrentStart()) return
      const taskId = taskIdFrom(result)
      if (!taskId) {
        throw new Error('启动响应缺少任务 ID')
      }
      onTaskCreated?.(result)
      navigate(`/tasks/${encodeURIComponent(taskId)}`)
    } catch (error) {
      if (isCurrentStart() && !isAborted(error, controller.signal)) setActionError(startErrorMessage(error))
    } finally {
      const wasCurrentStart = isCurrentStart()
      if (startControllerRef.current === controller) startControllerRef.current = null
      if (wasCurrentStart) setSaving(false)
    }
  }

  if (loading && coursesLoading && courses.length === 0) {
    return (
      <section className={cn('mx-auto w-full max-w-6xl px-4 py-8 md:px-8', className)} aria-labelledby="launch-title">
        <h1 id="launch-title" className="text-xl font-semibold tracking-tight">开始学习</h1>
        <p className="mt-2 text-sm text-label-secondary" role="status" aria-live="polite">正在加载课程与学习参数…</p>
      </section>
    )
  }

  if (loadError && courses.length === 0) {
    return (
      <section className={cn('mx-auto w-full max-w-6xl px-4 py-8 md:px-8', className)} aria-labelledby="launch-title">
        <h1 id="launch-title" className="text-xl font-semibold tracking-tight">开始学习</h1>
        <Alert className="mt-5 max-w-xl" variant="danger" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{loadError}</span>
            <Button type="button" variant="outline" onClick={() => loadPage()}>重试</Button>
          </div>
        </Alert>
      </section>
    )
  }

  return (
    <section className={cn('mx-auto w-full max-w-6xl px-4 py-7 md:px-8 md:py-8', className)} aria-labelledby="launch-title">
      <div>
        <div className="min-w-0">
          <p className="text-xs font-medium text-label-secondary">课程工作台</p>
          <h1 id="launch-title" className="mt-1 truncate text-balance text-xl font-semibold">
            {activeAccount?.name || '开始学习'}
          </h1>
          <p className="mt-1 text-pretty text-sm text-label-secondary">选择课程并确认这个账户的学习参数。</p>
        </div>
      </div>

      {loadError ? (
        <Alert className="mt-5" variant="warning" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{loadError}</span>
            <Button type="button" variant="outline" onClick={() => loadPage()}>重试</Button>
          </div>
        </Alert>
      ) : null}
      {answerError ? (
        <Alert className="mt-5" variant="warning" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{answerError}</span>
            <Button type="button" variant="outline" onClick={() => loadPage()}>重试</Button>
          </div>
        </Alert>
      ) : null}
      {actionError ? (
        <Alert className="mt-5" variant="danger" aria-live="polite">{actionError}</Alert>
      ) : null}

      <div className="mt-7 grid items-start gap-6 md:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]">
        <div className="min-w-0 border-y border-separator bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-separator px-4 py-3">
            <div>
              <h2 className="font-semibold">课程</h2>
              <p className="mt-0.5 text-xs text-label-secondary" aria-live="polite">
                已选择 {selectedCount} 门
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button type="button" size="sm" variant="ghost" onClick={selectAll} disabled={launchControlsDisabled || filteredCourses.length === 0}>全选</Button>
              <Button type="button" size="sm" variant="ghost" onClick={clearSelection} disabled={launchControlsDisabled || selectedCount === 0}>清空</Button>
              <Button type="button" size="sm" variant="outline" onClick={() => loadPage({ refresh: true })} loading={refreshing} disabled={launchControlsDisabled}>
                刷新课程
              </Button>
            </div>
          </div>

          <div className="border-b border-separator px-4 py-3">
            <Input
              type="search"
              aria-label="搜索课程"
              placeholder="搜索课程名称或 ID"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              disabled={launchControlsDisabled}
            />
          </div>

          {stale ? (
            <p className="border-b border-separator bg-warning/[0.08] px-4 py-2.5 text-sm text-label-primary" role="status" aria-live="polite">
              正在显示上次成功加载的课程
            </p>
          ) : null}

          {coursesLoading && courses.length === 0 ? (
            <p className="px-4 py-8 text-sm text-label-secondary" role="status" aria-live="polite">正在加载课程…</p>
          ) : filteredCourses.length === 0 ? (
            <p className="px-4 py-8 text-sm text-label-secondary">没有匹配的课程</p>
          ) : (
            <ul className="divide-y divide-separator" aria-label="课程列表">
              {filteredCourses.map((course) => (
                <li key={course.courseId}>
                  <label className="touch-target flex min-h-12 cursor-pointer items-center gap-3 px-4 py-2.5 text-sm hover:bg-black/[0.025] focus-within:bg-accent-blue/[0.05]">
                    <input
                      type="checkbox"
                      aria-label={course.title}
                      className="size-4 accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2"
                      checked={selectedSet.has(course.courseId)}
                      onChange={() => toggleCourse(course.courseId)}
                      disabled={launchControlsDisabled}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-label-primary">{course.title}</span>
                      <span className="mt-0.5 block truncate text-xs text-label-tertiary">{course.courseId}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        <aside className="min-w-0 border-y border-separator bg-surface md:sticky md:top-5" aria-labelledby="launch-inspector-title">
          <div className="border-b border-separator px-4 py-3">
            <h2 id="launch-inspector-title" className="font-semibold">本账户学习参数</h2>
            <p className="mt-0.5 text-pretty text-xs text-label-secondary">保存在当前账户中，启动任务时使用。</p>
          </div>
          <div className="space-y-5 px-4 py-4">
            <Field label="播放速度" htmlFor="launch-speed" description="范围 1.0 到 2.0 倍。">
              <Input
                id="launch-speed"
                type="number"
                inputMode="decimal"
                min="1"
                max="2"
                step="0.1"
                value={preferenceNumber(preferences.speed, DEFAULT_PREFERENCES.speed)}
                onChange={updatePreference('speed')}
                disabled={launchControlsDisabled}
              />
            </Field>

            <Field label="章节并发数" htmlFor="launch-jobs" description="每个账户同时处理的章节数。">
              <Input
                id="launch-jobs"
                type="number"
                inputMode="numeric"
                min="1"
                max="10"
                step="1"
                value={preferenceNumber(preferences.jobs, DEFAULT_PREFERENCES.jobs)}
                onChange={updatePreference('jobs')}
                disabled={launchControlsDisabled}
              />
            </Field>

            <Field label="未开放章节" htmlFor="launch-notopen-action">
              <select
                id="launch-notopen-action"
                value={preferences.notopen_action}
                onChange={updatePreference('notopen_action')}
                disabled={launchControlsDisabled}
                className="touch-target touch-target-compact flex h-9 w-full rounded-md border border-separator bg-surface px-2.5 py-1.5 text-sm text-label-primary outline-none focus-visible:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue/20"
              >
                <option value="retry">稍后重试</option>
                <option value="continue">跳过并继续</option>
              </select>
            </Field>

            <div className="space-y-3 border-t border-separator pt-4">
              <label className="touch-target flex min-h-11 cursor-pointer items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2"
                  checked={Boolean(preferences.answer_enabled)}
                  onChange={updatePreference('answer_enabled')}
                  disabled={launchControlsDisabled}
                />
                <span className="min-w-0">
                  <span className="block font-medium text-label-primary">启用答题</span>
                  <span className="mt-0.5 block text-xs text-label-secondary">使用设置中的共享答题连接。</span>
                </span>
              </label>

              {preferences.answer_enabled ? (
                <>
                  <Field label="答题覆盖率" htmlFor="launch-cover-rate" description="0 到 1 之间的小数。">
                    <Input
                      id="launch-cover-rate"
                      type="number"
                      inputMode="decimal"
                      min="0"
                      max="1"
                      step="0.05"
                      value={preferenceNumber(preferences.answer_cover_rate, DEFAULT_PREFERENCES.answer_cover_rate)}
                      onChange={updatePreference('answer_cover_rate')}
                      disabled={launchControlsDisabled}
                    />
                  </Field>
                  <label className="touch-target flex min-h-11 cursor-pointer items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2"
                      checked={Boolean(preferences.answer_auto_submit)}
                      onChange={updatePreference('answer_auto_submit')}
                      disabled={launchControlsDisabled}
                    />
                    <span className="font-medium text-label-primary">自动提交答案</span>
                  </label>
                  {connectionBlocked ? (
                    <p className="text-xs leading-5 text-danger" role="status" aria-live="polite">
                      请先在设置中测试答题连接
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>

            <div className="border-t border-separator pt-4">
              <Button
                type="button"
                className="w-full"
                onClick={handleStart}
                loading={saving}
                disabled={launchControlsDisabled || !enabled || Boolean(runningTask) || Boolean(connectionBlocked)}
              >
                开始学习
              </Button>
              {!enabled ? <p className="mt-2 text-xs text-danger">请先启用该账户</p> : null}
              {runningTask ? <p className="mt-2 text-xs text-label-secondary">该账户已有任务在运行</p> : null}
            </div>
          </div>
        </aside>
      </div>
    </section>
  )
}

export {
  DEFAULT_PREFERENCES,
  asCourses,
  asPreferences,
  answerConnectionReady,
  preferencePayload,
  startErrorMessage,
}
export default LaunchPage
