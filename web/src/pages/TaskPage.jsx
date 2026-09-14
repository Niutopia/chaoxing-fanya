import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowLeft, ChevronDown, ChevronRight, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { cancelTask, getTask, getTaskDetails, getTaskLogs } from '../api/tasks'
import usePolling from '../hooks/usePolling'
import Alert from '../components/ui/Alert'
import Button from '../components/ui/Button'
import TaskLog from '../components/tasks/TaskLog'
import TaskProgress from '../components/tasks/TaskProgress'
import TaskStatus, { taskStatusLabel } from '../components/tasks/TaskStatus'
import { cn } from '../lib/utils'

const ACTIVE_STATES = new Set(['running', 'stopping'])
const TERMINAL_STATES = new Set(['completed', 'failed', 'stopped'])

function unwrap(value, keys = []) {
  if (value && typeof value === 'object') {
    for (const key of keys) {
      if (value[key] !== undefined) return value[key]
    }
    if (value.data !== undefined) return value.data
  }
  return value
}

function scalarText(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' || typeof value === 'number') {
    const text = String(value).trim()
    return text ? text : null
  }
  return null
}

function scalarNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function numericMap(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.entries(value).reduce((result, [key, item]) => {
    if (typeof item === 'boolean') return result
    const number = Number(item)
    if (Number.isFinite(number)) result[key] = number
    return result
  }, {})
}

function timestampSeconds(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (Number.isFinite(number)) return Math.abs(number) > 100000000000 ? number / 1000 : number
  if (typeof value !== 'string') return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed / 1000 : null
}

function numberFrom(sources, keys) {
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue
    for (const key of keys) {
      if (source[key] === undefined || source[key] === null || source[key] === '') continue
      const number = Number(source[key])
      if (Number.isFinite(number) && number >= 0) return number
    }
  }
  return null
}

function isComplete(value) {
  const state = scalarText(value?.status ?? value?.state)?.toLowerCase()
  return state === 'completed' || state === 'complete' || state === 'done' || state === 'success' || state === 'succeeded'
}

function sumChapterValues(courses, selector) {
  return courses.reduce((sum, course) => sum + courseChapters(course).filter(selector).length, 0)
}

function aggregateCounts(snapshot, details) {
  const courses = Array.isArray(details?.courses) ? details.courses : []
  const jobs = details?.active_jobs && typeof details.active_jobs === 'object'
    ? Object.values(details.active_jobs).filter((job) => job && typeof job === 'object')
    : []
  const sources = [details?.counts, snapshot?.stats]
  const snapshotTotal = numberFrom([snapshot], ['total'])
  const courseTotal = numberFrom(sources, ['total_courses', 'courses_total'])
    ?? (courses.length > 0
      ? courses.length
      : (snapshotTotal > 0 ? snapshotTotal : null))
  let courseCompleted = numberFrom(sources, ['completed_courses', 'courses_completed'])
  if (courseCompleted === null && courses.length > 0) courseCompleted = courses.filter(isComplete).length
  if (courseCompleted === null && courses.length === 0 && ACTIVE_STATES.has(snapshot?.state)) {
    courseCompleted = numberFrom([snapshot], ['progress'])
  }
  if (courseCompleted === null && courseTotal !== null && snapshot?.state === 'completed') courseCompleted = courseTotal

  const chapterTotal = numberFrom(sources, ['total_chapters', 'chapters_total'])
    ?? (courses.length > 0 ? courses.reduce((sum, course) => sum + courseChapters(course).length, 0) : null)
  let chapterCompleted = numberFrom(sources, ['completed_chapters', 'chapters_completed'])
  if (chapterCompleted === null && courses.length > 0) chapterCompleted = sumChapterValues(courses, isComplete)
  if (chapterCompleted === null && chapterTotal !== null && snapshot?.state === 'completed') chapterCompleted = chapterTotal

  const taskTotal = numberFrom(sources, ['total_tasks', 'tasks_total'])
    ?? (jobs.length > 0 ? jobs.length : null)
  let taskCompleted = numberFrom(sources, ['completed_tasks', 'tasks_completed'])
  if (taskCompleted === null && taskTotal !== null && snapshot?.state === 'completed') taskCompleted = taskTotal

  return {
    courses: { completed: courseCompleted, total: courseTotal },
    chapters: { completed: chapterCompleted, total: chapterTotal },
    tasks: { completed: taskCompleted, total: taskTotal },
  }
}

function formatCountValue(value) {
  if (value === null || value === undefined) return '—'
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

function formatCountPair(pair) {
  if (!pair || (pair.completed === null && pair.total === null)) return '—'
  return `${formatCountValue(pair.completed)} / ${formatCountValue(pair.total)}`
}

function elapsedSeconds(snapshot, now = Date.now()) {
  const explicit = numberFrom([snapshot], ['elapsed_seconds', 'elapsedSeconds', 'duration_seconds', 'durationSeconds'])
  if (explicit !== null) return Math.round(explicit)
  const started = timestampSeconds(snapshot?.started_at ?? snapshot?.startedAt)
  if (started === null) return null
  const finished = timestampSeconds(snapshot?.finished_at ?? snapshot?.finishedAt)
  if (finished === null && !ACTIVE_STATES.has(snapshot?.state)) return null
  const end = finished ?? now / 1000
  return Math.max(0, Math.round(end - started))
}

function formatElapsed(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  const seconds = Math.max(0, Math.round(Number(value)))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = String(seconds % 60).padStart(2, '0')
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}`
    : `${minutes}:${remainder}`
}

function normalizeSnapshot(value, taskId = '') {
  const source = unwrap(value, ['task', 'snapshot'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) return null
  const id = scalarText(source.id ?? source.task_id ?? source.taskId) ?? String(taskId)
  const state = scalarText(source.state) ?? 'running'
  return {
    id,
    account_id: scalarText(source.account_id ?? source.accountId),
    state,
    progress: scalarNumber(source.progress),
    total: scalarNumber(source.total),
    current_course: scalarText(source.current_course ?? source.currentCourse),
    current_chapter: scalarText(source.current_chapter ?? source.currentChapter),
    current_task: scalarText(source.current_task ?? source.currentTask),
    error: safeErrorMessage({ message: scalarText(source.error) }, ''),
    started_at: source.started_at ?? source.startedAt ?? null,
    finished_at: source.finished_at ?? source.finishedAt ?? null,
    stats: numericMap(source.stats ?? source.counts),
  }
}

function normalizeDetails(value) {
  const source = unwrap(value, ['details'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { courses: [], active_jobs: {}, counts: {} }
  }
  const courses = Array.isArray(source.courses) ? source.courses.filter((course) => course && typeof course === 'object') : []
  const activeJobs = source.active_jobs ?? source.activeJobs
  return {
    courses,
    active_jobs: activeJobs && typeof activeJobs === 'object' && !Array.isArray(activeJobs) ? activeJobs : {},
    counts: source.counts && typeof source.counts === 'object' && !Array.isArray(source.counts) ? source.counts : {},
  }
}

function normalizeLogPage(value) {
  const source = unwrap(value, ['logs'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { items: [], nextCursor: null }
  }
  const items = Array.isArray(source.items)
    ? source.items
      .filter((item) => item && typeof item === 'object')
      .map((item) => ({
        sequence: item.sequence,
        level: item.level,
        message: item.message,
        timestamp: item.timestamp,
      }))
    : []
  const rawCursor = source.next_cursor ?? source.nextCursor
  const nextCursor = Number.isFinite(Number(rawCursor)) ? Number(rawCursor) : null
  return { items, nextCursor }
}

function isNotFound(error) {
  return error?.status === 404 || error?.code === 'task_not_found'
}

function safeErrorMessage(error, fallback) {
  const message = scalarText(error?.message)
  if (!message) return fallback
  return message
    .replace(/\b(?:bearer\s+)[^\s,;}]+/gi, 'Bearer [redacted]')
    .replace(/((?:password|passwd|pass|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|cookies|key))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,;&\s}]+)/gi, '$1=[redacted]')
}

function accountValue(account, snakeCase, camelCase = snakeCase) {
  return account?.[snakeCase] ?? account?.[camelCase]
}

function courseTitle(course) {
  return scalarText(course?.title ?? course?.name ?? course?.course_name ?? course?.courseName)
    ?? scalarText(course?.id ?? course?.course_id ?? course?.courseId)
    ?? '未命名课程'
}

function courseChapters(course) {
  const chapters = course?.chapters ?? course?.chapter_list ?? course?.chapterList
  return Array.isArray(chapters) ? chapters.filter((chapter) => chapter && typeof chapter === 'object') : []
}

function chapterTitle(chapter) {
  return scalarText(chapter?.title ?? chapter?.name ?? chapter?.chapter_name ?? chapter?.chapterName)
    ?? scalarText(chapter?.id ?? chapter?.chapter_id ?? chapter?.chapterId)
    ?? '未命名章节'
}

function jobName(job, key) {
  return scalarText(job?.job_name ?? job?.jobName ?? job?.name ?? job?.title) ?? String(key)
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(scalarNumber(value)))
  const minutes = Math.floor(seconds / 60)
  const remainder = String(seconds % 60).padStart(2, '0')
  return `${minutes}:${remainder}`
}

function taskAccountLabel(snapshot, account, accounts) {
  const supplied = account
    ?? (Array.isArray(accounts)
      ? accounts.find((item) => String(item?.id) === String(snapshot?.account_id))
      : null)
  return scalarText(supplied?.name) ?? scalarText(snapshot?.account_name ?? snapshot?.accountName) ?? snapshot?.account_id ?? '当前账户'
}

function currentJobLabel(activeJobs) {
  const first = Object.entries(activeJobs ?? {})[0]
  return first ? jobName(first[1], first[0]) : null
}

function UnknownTask({ accountId }) {
  return (
    <section className="mx-auto w-full max-w-4xl px-4 py-8 md:px-8" aria-labelledby="task-not-found-title">
      <Link
        to="/"
        className="touch-target touch-target-compact inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 text-sm text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
      >
        <ArrowLeft aria-hidden="true" size={15} strokeWidth={1.8} />
        返回任务总览
      </Link>
      <h1 id="task-not-found-title" className="mt-8 text-xl font-semibold tracking-tight">任务不存在</h1>
      <p className="mt-2 max-w-md text-sm leading-5 text-label-secondary">
        这个任务可能已经被清理，或任务链接已失效。
      </p>
      {accountId ? (
        <Link
          to={`/accounts/${encodeURIComponent(accountId)}/launch`}
          className="touch-target touch-target-compact mt-5 inline-flex min-h-9 items-center rounded-md border border-separator bg-surface px-3 text-sm text-label-primary hover:bg-black/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
        >
          返回课程启动
        </Link>
      ) : null}
    </section>
  )
}

function TaskCourseList({ courses, expanded, onToggle }) {
  if (courses.length === 0) {
    return <p className="px-4 py-6 text-sm text-label-secondary">暂无课程详情</p>
  }

  return (
    <div className="divide-y divide-separator">
      {courses.map((course, index) => {
        const id = scalarText(course.id ?? course.course_id ?? course.courseId) ?? `course-${index}`
        const title = courseTitle(course)
        const isExpanded = expanded.has(id)
        const chapters = courseChapters(course)
        return (
          <div key={id}>
            <button
              type="button"
              className="touch-target flex min-h-12 w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-black/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
              aria-expanded={isExpanded}
              aria-label={isExpanded ? `收起${title}` : `展开${title}`}
              aria-controls={`task-course-${id}`}
              onClick={() => onToggle(id)}
            >
              {isExpanded
                ? <ChevronDown aria-hidden="true" size={16} strokeWidth={1.8} />
                : <ChevronRight aria-hidden="true" size={16} strokeWidth={1.8} />}
              <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
              <span className="shrink-0 text-xs text-label-tertiary">{chapters.length} 章节</span>
              <span className="sr-only">{isExpanded ? `收起${title}` : `展开${title}`}</span>
            </button>
            {isExpanded ? (
              <div id={`task-course-${id}`} className="border-t border-separator bg-black/[0.018] px-4 py-2.5">
                {chapters.length === 0 ? (
                  <p className="text-xs text-label-secondary">暂无章节详情</p>
                ) : (
                  <ul className="space-y-1.5">
                    {chapters.map((chapter, chapterIndex) => {
                      const titleText = chapterTitle(chapter)
                      const status = taskStatusLabel(chapter.status)
                      return (
                        <li key={scalarText(chapter.id ?? chapter.chapter_id) ?? `${id}-chapter-${chapterIndex}`} className="flex items-center justify-between gap-3 text-sm">
                          <span className="min-w-0 truncate text-label-primary">{titleText}</span>
                          <span className="shrink-0 text-xs text-label-secondary">{status}</span>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function CancelDialog({ open, accountLabel, courseLabel, pending, error, triggerRef, focusFallbackRef, onOpenChange, onConfirm }) {
  const continueRef = useRef(null)

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(440px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-separator bg-surface p-5 text-label-primary outline-none"
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            continueRef.current?.focus()
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            const trigger = triggerRef?.current
            if (trigger && !trigger.disabled && document.contains(trigger)) {
              trigger.focus()
              return
            }
            const fallback = focusFallbackRef?.current
            if (fallback && !fallback.disabled && document.contains(fallback)) fallback.focus()
          }}
          onEscapeKeyDown={(event) => {
            if (pending) event.preventDefault()
          }}
          onPointerDownOutside={(event) => {
            if (pending) event.preventDefault()
          }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold tracking-tight">
                确认停止任务
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm leading-5 text-label-secondary">
                将停止账户“{accountLabel}”正在处理的课程“{courseLabel}”。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="touch-target touch-target-compact inline-flex size-8 shrink-0 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                aria-label="关闭停止确认对话框"
                disabled={pending}
              >
                <X aria-hidden="true" size={17} strokeWidth={1.8} />
              </button>
            </Dialog.Close>
          </div>
          {error ? <Alert className="mt-4" variant="danger" aria-live="polite">{error}</Alert> : null}
          <div className="mt-5 flex justify-end gap-2 border-t border-separator pt-4">
            <Dialog.Close asChild>
              <Button ref={continueRef} type="button" variant="ghost" disabled={pending}>
                继续运行
              </Button>
            </Dialog.Close>
            <Button type="button" variant="destructive" loading={pending} onClick={onConfirm}>
              确认停止
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function TaskPage({ account, accounts = [], onSnapshot, className }) {
  const { taskId: routeTaskId } = useParams()
  const taskId = routeTaskId ?? ''
  const taskGenerationRef = useRef({ taskId, generation: 0 })
  if (taskGenerationRef.current.taskId !== taskId) {
    taskGenerationRef.current = {
      taskId,
      generation: taskGenerationRef.current.generation + 1,
    }
  }
  const [snapshot, setSnapshot] = useState(null)
  const [details, setDetails] = useState({ courses: [], active_jobs: {}, counts: {} })
  const [logs, setLogs] = useState([])
  const [notFound, setNotFound] = useState(false)
  const [snapshotError, setSnapshotError] = useState(false)
  const [detailsError, setDetailsError] = useState(false)
  const [logsError, setLogsError] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const [expandedCourses, setExpandedCourses] = useState(() => new Set())
  const [clockNow, setClockNow] = useState(() => Date.now())
  const cursorRef = useRef(0)
  const seenSequencesRef = useRef(new Set())
  const cancelRequestedRef = useRef(false)
  const cancelTriggerRef = useRef(null)
  const monitorBackLinkRef = useRef(null)

  const currentGeneration = taskGenerationRef.current.generation

  useEffect(() => {
    setSnapshot(null)
    setDetails({ courses: [], active_jobs: {}, counts: {} })
    setLogs([])
    setNotFound(false)
    setSnapshotError(false)
    setDetailsError(false)
    setLogsError(false)
    setRequestError('')
    setConfirmCancel(false)
    setCancelling(false)
    setCancelError('')
    setExpandedCourses(new Set())
    setClockNow(Date.now())
    cursorRef.current = 0
    seenSequencesRef.current = new Set()
    cancelRequestedRef.current = false
  }, [taskId])

  useEffect(() => {
    if (!snapshot || !ACTIVE_STATES.has(snapshot.state)) return undefined
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [snapshot?.state])

  const loadSnapshotAndDetails = useCallback(async () => {
    const [snapshotResult, detailsResult] = await Promise.allSettled([
      getTask(taskId),
      getTaskDetails(taskId),
    ])
    if (snapshotResult.status === 'rejected') throw snapshotResult.reason
    return {
      snapshot: normalizeSnapshot(snapshotResult.value, taskId),
      details: detailsResult.status === 'fulfilled' ? normalizeDetails(detailsResult.value) : null,
      detailsError: detailsResult.status === 'rejected' ? detailsResult.reason : null,
    }
  }, [taskId])

  const monitorEnabled = Boolean(taskId) && !notFound && (snapshot === null || ACTIVE_STATES.has(snapshot.state))

  const handleSnapshotData = useCallback((result) => {
    const nextSnapshot = result?.snapshot
    if (!nextSnapshot) return
    setSnapshot(nextSnapshot)
    setSnapshotError(false)
    setRequestError('')
    setNotFound(false)
    if (result.details) {
      setDetails(result.details)
      setDetailsError(false)
    } else if (result.detailsError) {
      setDetailsError(true)
    }
    onSnapshot?.(nextSnapshot)
  }, [onSnapshot])

  const handleSnapshotError = useCallback((error) => {
    if (isNotFound(error)) {
      setNotFound(true)
      setRequestError('')
      return
    }
    setSnapshotError(true)
    setRequestError(safeErrorMessage(error, '任务状态加载失败'))
  }, [])

  usePolling(loadSnapshotAndDetails, {
    enabled: monitorEnabled,
    intervalMs: 2000,
    onData: handleSnapshotData,
    onError: handleSnapshotError,
  })

  const loadLogs = useCallback(async () => {
    const page = await getTaskLogs(taskId, { after: cursorRef.current })
    return normalizeLogPage(page)
  }, [taskId])

  const handleLogsData = useCallback((page) => {
    if (!page) return
    if (page.nextCursor !== null) cursorRef.current = Math.max(cursorRef.current, page.nextCursor)
    setLogs((current) => {
      const next = [...current]
      for (const item of page.items) {
        const sequence = Number(item?.sequence)
        const key = Number.isFinite(sequence) ? sequence : `${item?.timestamp ?? ''}:${item?.message ?? ''}`
        if (seenSequencesRef.current.has(key)) continue
        seenSequencesRef.current.add(key)
        next.push(item)
      }
      next.sort((left, right) => {
        const leftSequence = Number(left?.sequence)
        const rightSequence = Number(right?.sequence)
        if (Number.isFinite(leftSequence) && Number.isFinite(rightSequence)) return leftSequence - rightSequence
        return 0
      })
      return next
    })
    setLogsError(false)
  }, [])

  const handleLogsError = useCallback((error) => {
    if (isNotFound(error)) {
      setNotFound(true)
      return
    }
    setLogsError(true)
  }, [])

  usePolling(loadLogs, {
    enabled: monitorEnabled,
    intervalMs: 2000,
    onData: handleLogsData,
    onError: handleLogsError,
  })

  const accountLabel = taskAccountLabel(snapshot, account, accounts)
  const courseLabel = snapshot?.current_course
    ?? (details.courses.length > 0 ? courseTitle(details.courses[0]) : '当前课程')
  const activeJobLabel = currentJobLabel(details.active_jobs)
  const currentVideo = snapshot?.current_task ?? activeJobLabel
  const reconnecting = snapshotError || detailsError || logsError
  const isStopping = Boolean(snapshot && (snapshot.state === 'stopping' || cancelling))
  const canCancel = Boolean(snapshot && ACTIVE_STATES.has(snapshot.state) && !notFound)
  const ownerAccountId = snapshot?.account_id ?? account?.id
  const launchHref = ownerAccountId
    ? `/accounts/${encodeURIComponent(ownerAccountId)}/launch`
    : null
  const counts = useMemo(() => aggregateCounts(snapshot, details), [snapshot, details])
  const elapsed = formatElapsed(elapsedSeconds(snapshot, clockNow))

  const jobs = useMemo(
    () => Object.entries(details.active_jobs ?? {}).filter(([, job]) => job && typeof job === 'object'),
    [details.active_jobs],
  )

  const toggleCourse = (courseId) => {
    setExpandedCourses((current) => {
      const next = new Set(current)
      if (next.has(courseId)) next.delete(courseId)
      else next.add(courseId)
      return next
    })
  }

  const handleCancel = async () => {
    if (!canCancel || cancelling || cancelRequestedRef.current) return
    const requestTaskId = taskId
    const requestGeneration = currentGeneration
    const isCurrentRequest = () => (
      taskGenerationRef.current.taskId === requestTaskId
      && taskGenerationRef.current.generation === requestGeneration
    )
    cancelRequestedRef.current = true
    setCancelling(true)
    setCancelError('')
    try {
      const result = await cancelTask(requestTaskId)
      if (!isCurrentRequest()) return
      const nextSnapshot = normalizeSnapshot(result, taskId) ?? { ...snapshot, state: 'stopping' }
      setSnapshot(nextSnapshot)
      onSnapshot?.(nextSnapshot)
      setConfirmCancel(false)
    } catch (error) {
      if (!isCurrentRequest()) return
      cancelRequestedRef.current = false
      setCancelling(false)
      if (isNotFound(error)) {
        setConfirmCancel(false)
        setNotFound(true)
      } else {
        setCancelError(safeErrorMessage(error, '停止任务失败，请重试'))
      }
    } finally {
      if (!isCurrentRequest()) return
    }
  }

  if (notFound) return <UnknownTask accountId={snapshot?.account_id ?? account?.id} />

  if (!snapshot) {
    return (
      <section className={cn('mx-auto w-full max-w-4xl px-4 py-8 md:px-8', className)} aria-labelledby="task-loading-title">
        <h1 id="task-loading-title" className="text-xl font-semibold tracking-tight">任务详情</h1>
        {reconnecting ? <Alert className="mt-5 max-w-xl" variant="warning" aria-live="polite">正在重新连接</Alert> : null}
        {requestError ? <Alert className="mt-5 max-w-xl" variant="danger" aria-live="polite">{requestError}</Alert> : null}
        {!reconnecting && !requestError ? <p className="mt-2 text-sm text-label-secondary" role="status" aria-live="polite">正在加载任务…</p> : null}
      </section>
    )
  }

  return (
    <section className={cn('mx-auto w-full max-w-5xl px-4 py-7 md:px-8 md:py-8', className)} aria-labelledby="task-title">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            ref={monitorBackLinkRef}
            to="/"
            className="touch-target touch-target-compact inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 text-sm text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          >
            <ArrowLeft aria-hidden="true" size={15} strokeWidth={1.8} />
            返回任务总览
          </Link>
          <p className="mt-5 text-xs font-medium text-label-secondary">任务监视器</p>
          <h1 id="task-title" className="mt-1 truncate text-xl font-semibold tracking-tight">{accountLabel}</h1>
          <p className="mt-1 truncate text-sm text-label-secondary">任务 {snapshot.id || taskId}</p>
        </div>
        <div className="flex items-center gap-3">
          <TaskStatus state={snapshot.state} />
          {canCancel ? (
            <Button
              ref={cancelTriggerRef}
              type="button"
              variant="destructive"
              disabled={isStopping}
              onClick={() => {
                setCancelError('')
                setConfirmCancel(true)
              }}
            >
              {isStopping ? '正在停止' : '停止任务'}
            </Button>
          ) : null}
          {TERMINAL_STATES.has(snapshot.state) && launchHref ? (
            <Link
              to={launchHref}
              className="touch-target touch-target-compact inline-flex min-h-9 items-center rounded-md px-2.5 text-sm text-accent-blue hover:bg-accent-blue/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              返回课程启动
            </Link>
          ) : null}
        </div>
      </header>

      {reconnecting ? <Alert className="mt-5" variant="warning" aria-live="polite">正在重新连接</Alert> : null}
      {snapshot.error ? <Alert className="mt-5" variant="danger" aria-live="polite">{snapshot.error}</Alert> : null}

      <div className="mt-7 grid items-start gap-6 md:grid-cols-[minmax(0,1.1fr)_minmax(260px,0.9fr)]">
        <section className="min-w-0 border-y border-separator bg-surface" aria-labelledby="task-progress-title">
          <div className="border-b border-separator px-4 py-3">
            <h2 id="task-progress-title" className="font-semibold">任务进度</h2>
            <p className="mt-0.5 text-xs text-label-secondary">当前账户的实时学习快照。</p>
          </div>
          <div className="space-y-5 px-4 py-4">
            <TaskProgress progress={snapshot.progress} total={snapshot.total} label="总进度" />
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 border-t border-separator pt-4 text-sm">
              <dt className="text-label-secondary">账户</dt>
              <dd className="min-w-0 truncate text-right font-medium">{accountLabel}</dd>
              <dt className="text-label-secondary">当前课程</dt>
              <dd className="min-w-0 truncate text-right font-medium">{snapshot.current_course ?? '—'}</dd>
              <dt className="text-label-secondary">当前章节</dt>
              <dd className="min-w-0 truncate text-right font-medium">{snapshot.current_chapter ?? '—'}</dd>
              <dt className="text-label-secondary">当前视频</dt>
              <dd className="min-w-0 truncate text-right font-medium">{currentVideo ?? '—'}</dd>
              <dt className="text-label-secondary">已用时间</dt>
              <dd aria-label="任务已用时间" className="min-w-0 truncate text-right font-medium tabular-nums">{elapsed}</dd>
            </dl>
          </div>
        </section>

        <section className="min-w-0 border-y border-separator bg-surface" aria-labelledby="task-jobs-title">
          <div className="border-b border-separator px-4 py-3">
            <h2 id="task-jobs-title" className="font-semibold">当前作业</h2>
            <p className="mt-0.5 text-xs text-label-secondary">视频和章节任务的细节。</p>
          </div>
          {jobs.length === 0 ? (
            <p className="px-4 py-6 text-sm text-label-secondary">暂无进行中的作业</p>
          ) : (
            <div className="divide-y divide-separator">
              {jobs.map(([key, job]) => {
                const label = jobName(job, key)
                const currentTime = job.current_time ?? job.currentTime
                const duration = job.duration
                return (
                  <div key={key} className="space-y-2 px-4 py-4">
                    <TaskProgress value={job.progress} label={label} showCount={false} />
                    {currentTime !== undefined || duration !== undefined ? (
                      <p className="text-right text-xs tabular-nums text-label-secondary">
                        {formatDuration(currentTime)} / {formatDuration(duration)}
                      </p>
                    ) : null}
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>

      <section className="mt-6 border-y border-separator bg-surface" aria-labelledby="task-counts-title">
        <div className="border-b border-separator px-4 py-3">
          <h2 id="task-counts-title" className="font-semibold">任务统计</h2>
          <p className="mt-0.5 text-xs text-label-secondary">当前账户本次任务的聚合数量。</p>
        </div>
        <dl className="grid gap-4 px-4 py-4 sm:grid-cols-3">
          <div>
            <dt className="text-xs text-label-secondary">课程</dt>
            <dd aria-label="已完成课程数量" className="mt-1 text-sm font-semibold tabular-nums">{formatCountPair(counts.courses)}</dd>
          </div>
          <div>
            <dt className="text-xs text-label-secondary">章节</dt>
            <dd aria-label="已完成章节数量" className="mt-1 text-sm font-semibold tabular-nums">{formatCountPair(counts.chapters)}</dd>
          </div>
          <div>
            <dt className="text-xs text-label-secondary">任务</dt>
            <dd aria-label="已完成任务数量" className="mt-1 text-sm font-semibold tabular-nums">{formatCountPair(counts.tasks)}</dd>
          </div>
        </dl>
      </section>

      <section className="mt-6 border-y border-separator bg-surface" aria-labelledby="task-courses-title">
        <div className="border-b border-separator px-4 py-3">
          <h2 id="task-courses-title" className="font-semibold">课程详情</h2>
          <p className="mt-0.5 text-xs text-label-secondary">展开课程查看章节状态。</p>
        </div>
        <TaskCourseList courses={details.courses} expanded={expandedCourses} onToggle={toggleCourse} />
      </section>

      <section className="mt-6 border-y border-separator bg-surface" aria-labelledby="task-logs-title">
        <div className="border-b border-separator px-4 py-3">
          <h2 id="task-logs-title" className="font-semibold">任务日志</h2>
          <p className="mt-0.5 text-xs text-label-secondary">日志只属于当前任务，并按游标增量加载。</p>
        </div>
        <TaskLog items={logs} />
      </section>

      <CancelDialog
        open={confirmCancel}
        accountLabel={accountLabel}
        courseLabel={courseLabel}
        pending={cancelling}
        error={cancelError}
        triggerRef={cancelTriggerRef}
        focusFallbackRef={monitorBackLinkRef}
        onOpenChange={(open) => {
          if (!cancelling) setConfirmCancel(open)
        }}
        onConfirm={handleCancel}
      />
    </section>
  )
}

export {
  ACTIVE_STATES,
  TERMINAL_STATES,
  normalizeDetails,
  normalizeLogPage,
  normalizeSnapshot,
  safeErrorMessage,
}
export default TaskPage
