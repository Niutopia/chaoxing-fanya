import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowLeft, ChevronDown, ChevronRight, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { cancelTask, getTask, getTaskDetails, getTaskLogs, refreshAnswerReport } from '../api/tasks'
import usePolling from '../hooks/usePolling'
import Alert from '../components/ui/Alert'
import Button from '../components/ui/Button'
import TaskLog from '../components/tasks/TaskLog'
import AnswerReport from '../components/tasks/AnswerReport'
import TaskProgress from '../components/tasks/TaskProgress'
import TaskStatus, { taskStatusLabel } from '../components/tasks/TaskStatus'
import { cn } from '../lib/utils'

const ACTIVE_STATES = new Set(['running', 'stopping'])
const TERMINAL_STATES = new Set(['completed', 'failed', 'stopped'])
const KNOWN_TASK_STATES = new Set([...ACTIVE_STATES, ...TERMINAL_STATES])

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
  const id = scalarText(source.id) ?? scalarText(source.task_id) ?? scalarText(source.taskId)
  const routeId = scalarText(taskId)
  const state = scalarText(source.state)?.toLowerCase()
  // A task response is only useful when it identifies the task that the
  // route is currently displaying. Never synthesize an id from the route or
  // a running state from an empty/partial response: doing so can make `{}`
  // look like a live task and keep polling forever.
  if (!id || !routeId || id !== routeId || !state || !KNOWN_TASK_STATES.has(state)) return null
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
  const serializedCourses = JSON.stringify(source.courses ?? [])
  return {
    courses,
    answer_report: source.answer_report && typeof source.answer_report === 'object' ? source.answer_report : {},
    truncated: serializedCourses.includes('[redacted-depth]')
      || serializedCourses.includes('[redacted-size]'),
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

function invalidSnapshotError() {
  const error = new Error('任务状态响应格式异常')
  error.code = 'task_response_invalid'
  return error
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
  if (course?.title === '[redacted-depth]' || course?.id === '[redacted-depth]') return '历史课程详情缺失'
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

function formatDate(value) {
  const seconds = timestampSeconds(value)
  if (seconds === null) return '时间未记录'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(seconds * 1000))
}

function unfinishedCourses(courses, terminal) {
  const needsAttention = (item) => terminal
    ? !isComplete(item)
    : ['failed', 'blocked', 'not_open', 'skipped'].includes(item?.status)
  return courses.flatMap((course) => {
    const chapters = courseChapters(course).filter(needsAttention)
    return chapters.length || needsAttention(course) ? [{ course, chapters }] : []
  })
}

function issueExplanation(item) {
  const checked = item?.diagnosis
  if (checked?.reason && checked?.checked_at) {
    return { reason: checked.reason, action: checked.next_action, checkedAt: checked.checked_at }
  }
  if (scalarText(item?.reason)) return { reason: item.reason, action: item.next_action }
  const fallbacks = {
    blocked: ['平台条件尚未满足', '在学习通确认开放状态后，再运行该课程。'],
    not_open: ['章节未开放，可能需要先完成前置章节', '在学习通查看开放时间及前置要求，满足后再运行。'],
    skipped: ['该任务仍需手动完成', '在学习通检查是否有待提交的答案或未完成的任务点。'],
    pending: ['本次尚未处理到这里', '返回课程启动，重新选择该课程继续。'],
    running: ['运行结束前未确认完成', '在学习通确认进度后，重新运行该课程。'],
    stopped: ['处理已停止，尚未确认完成', '返回课程启动，重新选择该课程继续。'],
  }
  const [reason, action] = fallbacks[item?.status] ?? [
    '执行失败，这条记录没有保存具体原因', '查看下方运行日志，确认原因后再重试。',
  ]
  return { reason, action }
}

function UnfinishedList({ groups }) {
  return (
    <div className="divide-y divide-separator">
      {groups.map(({ course, chapters }, courseIndex) => {
        const entries = chapters.length ? chapters : [course]
        const explanations = entries.flatMap((chapter) => {
          const jobs = Array.isArray(chapter.jobs) ? chapter.jobs.filter((job) => job && !isComplete(job)) : []
          return (jobs.length ? jobs : [chapter]).map(issueExplanation)
        })
        const actions = [...new Set(explanations.map((item) => item.action).filter(Boolean))]
        return (
          <div key={course.id ?? courseIndex} className="px-5 py-4">
            <h3 className="text-balance text-sm font-semibold">{courseTitle(course)}</h3>
            <ul className="mt-3 space-y-3">
              {entries.map((chapter, chapterIndex) => {
                const jobs = Array.isArray(chapter.jobs) ? chapter.jobs.filter((job) => job && !isComplete(job)) : []
                return (
                  <li key={chapter.id ?? chapterIndex} className="grid gap-x-4 border-l-2 border-warning/50 pl-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
                    <p className="text-pretty text-sm font-medium">{chapters.length ? chapterTitle(chapter) : '课程未完成，缺少章节详情'}</p>
                    <div>{(jobs.length ? jobs : [chapter]).map((job, jobIndex) => {
                      const explanation = issueExplanation(job)
                      return (
                        <div key={job.id ?? jobIndex} className="text-pretty text-sm leading-6 text-label-secondary">
                          {jobs.length > 1 ? <span>{jobName(job, '任务点')} · </span> : null}
                          <span className="text-label-primary">{safeErrorMessage({ message: explanation.reason }, '')}</span>
                          {explanation.checkedAt ? <span className="ml-2 text-xs">（{formatDate(explanation.checkedAt)}复查）</span> : null}
                        </div>
                      )
                    })}</div>
                  </li>
                )
              })}
            </ul>
            {actions.length ? (
              <div className="mt-4 rounded-md bg-canvas px-3 py-2.5 text-pretty text-sm leading-6">
                <span className="font-medium">下一步：</span>{actions.map((action) => safeErrorMessage({ message: action }, '')).join(' ')}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
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
              <span className="shrink-0 text-xs tabular-nums text-label-secondary">{chapters.filter(isComplete).length} / {chapters.length} 章</span>
              <TaskStatus state={course.status ?? 'pending'} className="shrink-0 text-xs" />
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
  const [snapshotFormatError, setSnapshotFormatError] = useState(false)
  const [detailsError, setDetailsError] = useState(false)
  const [logsError, setLogsError] = useState(false)
  const [terminalLogsLoaded, setTerminalLogsLoaded] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const [refreshingGrades, setRefreshingGrades] = useState(false)
  const [gradesError, setGradesError] = useState('')
  const gradeControllerRef = useRef(null)
  const [expandedCourses, setExpandedCourses] = useState(() => new Set())
  const [clockNow, setClockNow] = useState(() => Date.now())
  const cursorRef = useRef(0)
  const seenSequencesRef = useRef(new Set())
  const cancelRequestedRef = useRef(false)
  const cancelTriggerRef = useRef(null)
  const monitorBackLinkRef = useRef(null)
  const cancelControllerRef = useRef(null)

  const currentGeneration = taskGenerationRef.current.generation

  useEffect(() => {
    setSnapshot(null)
    setDetails({ courses: [], active_jobs: {}, counts: {} })
    setLogs([])
    setNotFound(false)
    setSnapshotError(false)
    setSnapshotFormatError(false)
    setDetailsError(false)
    setLogsError(false)
    setTerminalLogsLoaded(false)
    setRequestError('')
    setConfirmCancel(false)
    setCancelling(false)
    setCancelError('')
    setRefreshingGrades(false)
    setGradesError('')
    gradeControllerRef.current?.abort()
    setExpandedCourses(new Set())
    setClockNow(Date.now())
    cursorRef.current = 0
    seenSequencesRef.current = new Set()
    cancelRequestedRef.current = false
    cancelControllerRef.current?.abort()
    cancelControllerRef.current = null
    return () => {
      gradeControllerRef.current?.abort()
      cancelControllerRef.current?.abort()
      cancelControllerRef.current = null
    }
  }, [taskId])

  useEffect(() => {
    if (!snapshot || !ACTIVE_STATES.has(snapshot.state)) return undefined
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [snapshot?.state])

  const loadSnapshotAndDetails = useCallback(async (signal) => {
    // Start both requests together so the details panel keeps its existing
    // latency, but validate the authoritative snapshot as soon as it settles.
    // A malformed snapshot therefore stops this polling round immediately
    // instead of waiting for a potentially stalled details request.
    const snapshotPromise = getTask(taskId, { signal })
    const detailsPromise = Promise.resolve(getTaskDetails(taskId, { signal }))
      .then(
        (value) => ({ status: 'fulfilled', value }),
        (reason) => ({ status: 'rejected', reason }),
      )
    const snapshotValue = await snapshotPromise
    const nextSnapshot = normalizeSnapshot(snapshotValue, taskId)
    if (!nextSnapshot) throw invalidSnapshotError()
    const detailsResult = await detailsPromise
    return {
      snapshot: nextSnapshot,
      details: detailsResult.status === 'fulfilled' ? normalizeDetails(detailsResult.value) : null,
      detailsError: detailsResult.status === 'rejected' ? detailsResult.reason : null,
    }
  }, [taskId])

  const monitorEnabled = Boolean(taskId)
    && !notFound
    && !snapshotFormatError
    && (snapshot === null || ACTIVE_STATES.has(snapshot.state) || detailsError || details.answer_report?.status === 'running')

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
    if (error?.code === 'task_response_invalid') setSnapshotFormatError(true)
  }, [])

  usePolling(loadSnapshotAndDetails, {
    enabled: monitorEnabled,
    intervalMs: 2000,
    onData: handleSnapshotData,
    onError: handleSnapshotError,
  })

  const terminalState = TERMINAL_STATES.has(snapshot?.state)
  const loadLogs = useCallback(async (signal) => {
    const page = await getTaskLogs(taskId, { after: cursorRef.current, signal })
    return { ...normalizeLogPage(page), terminalRead: terminalState }
  }, [taskId, terminalState])

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
    if (page.terminalRead) setTerminalLogsLoaded(true)
  }, [])

  const handleLogsError = useCallback((error) => {
    if (isNotFound(error)) {
      setNotFound(true)
      return
    }
    setLogsError(true)
  }, [])

  usePolling(loadLogs, {
    enabled: Boolean(taskId) && !notFound && !snapshotFormatError
      && (!terminalState || !terminalLogsLoaded),
    intervalMs: 2000,
    onData: handleLogsData,
    onError: handleLogsError,
  })

  const accountLabel = taskAccountLabel(snapshot, account, accounts)
  const courseLabel = snapshot?.current_course
    ?? (details.courses.length > 0 ? courseTitle(details.courses[0]) : '当前课程')
  const reconnecting = snapshotError || detailsError || logsError
  const isStopping = Boolean(snapshot && (snapshot.state === 'stopping' || cancelling))
  const canCancel = Boolean(snapshot && ACTIVE_STATES.has(snapshot.state) && !notFound)
  const ownerAccountId = snapshot?.account_id ?? account?.id
  const launchHref = ownerAccountId
    ? `/accounts/${encodeURIComponent(ownerAccountId)}/launch`
    : null
  const counts = useMemo(() => aggregateCounts(snapshot, details), [snapshot, details])
  const elapsed = formatElapsed(elapsedSeconds(snapshot, clockNow))
  const unfinished = unfinishedCourses(details.courses, terminalState)
  const unfinishedChapters = unfinished.reduce((total, group) => total + group.chapters.length, 0)
  const resultHeading = snapshot?.state === 'completed'
    ? '本次所选课程已全部完成'
    : snapshot?.state === 'stopped'
      ? '运行已停止，可稍后继续'
      : terminalState
        ? unfinishedChapters > 0
          ? `本次运行结束，仍有 ${unfinishedChapters} 个章节未完成`
          : '本次运行未完成'
        : isStopping ? '正在停止，请稍候' : '正在学习所选课程'

  const jobs = useMemo(
    () => terminalState ? [] : Object.entries(details.active_jobs ?? {}).filter(([, job]) => job && typeof job === 'object'),
    [details.active_jobs, terminalState],
  )

  const toggleCourse = (courseId) => {
    setExpandedCourses((current) => {
      const next = new Set(current)
      if (next.has(courseId)) next.delete(courseId)
      else next.add(courseId)
      return next
    })
  }

  const handleRefreshGrades = async () => {
    if (refreshingGrades || details.answer_report?.status === 'running') return
    setRefreshingGrades(true)
    setGradesError('')
    const generation = currentGeneration
    const controller = new AbortController()
    gradeControllerRef.current = controller
    try {
      const next = normalizeDetails(await refreshAnswerReport(taskId, { signal: controller.signal }))
      if (taskGenerationRef.current.generation !== generation || controller.signal.aborted) return
      setDetails(next)
      const updated = { ...snapshot, stats: { ...snapshot.stats, ...next.counts } }
      setSnapshot(updated)
      onSnapshot?.(updated)
    } catch (error) {
      if (taskGenerationRef.current.generation === generation && !controller.signal.aborted) setGradesError(safeErrorMessage(error, '刷新判分失败，请重试'))
    } finally {
      if (taskGenerationRef.current.generation === generation) setRefreshingGrades(false)
    }
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
    const controller = new AbortController()
    cancelControllerRef.current = controller
    try {
      const result = await cancelTask(requestTaskId, { signal: controller.signal })
      if (!isCurrentRequest()) return
      const nextSnapshot = normalizeSnapshot(result, requestTaskId)
      if (!nextSnapshot) throw invalidSnapshotError()
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
      if (cancelControllerRef.current === controller) cancelControllerRef.current = null
      if (isCurrentRequest()) {
        cancelRequestedRef.current = false
        setCancelling(false)
      }
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
          <h1 id="task-title" className="mt-4 text-balance text-xl font-semibold">{accountLabel} · {terminalState ? '学习结果' : '学习进度'}</h1>
          <p className="mt-2 text-sm tabular-nums text-label-secondary">
            {formatDate(snapshot.started_at)} 开始 · 用时 <span aria-label="任务已用时间">{elapsed}</span>
          </p>
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
      {snapshotFormatError && requestError ? <Alert className="mt-5" variant="danger" aria-live="polite">{requestError}</Alert> : null}

      <section className="mt-6 rounded-lg border border-separator bg-surface" aria-labelledby="task-progress-title">
        <div className="px-5 pt-5">
          <h2 id="task-progress-title" className="text-balance text-lg font-semibold">{resultHeading}</h2>
          {snapshot.error && !/^有 \d+ 门课程尚未完成/.test(snapshot.error) ? <p className="mt-2 text-pretty text-sm leading-6 text-label-secondary">{snapshot.error}</p> : null}
          {!terminalState && jobs.length === 0 ? (
            <p className="mt-2 text-pretty text-sm text-label-secondary">
              {snapshot.current_course ? `正在检查：${snapshot.current_course}${snapshot.current_chapter ? ` · ${snapshot.current_chapter}` : ''}` : '正在连接平台并读取课程，请稍候。'}
            </p>
          ) : null}
        </div>
        <dl className="grid grid-cols-1 gap-4 px-5 py-5 sm:grid-cols-3">
          <div>
            <dt className="text-sm text-label-secondary">已完成课程</dt>
            <dd aria-label="已完成课程数量" className="mt-1 text-2xl font-semibold tabular-nums">{formatCountPair(counts.courses)}</dd>
          </div>
          <div>
            <dt className="text-sm text-label-secondary">已完成章节</dt>
            <dd aria-label="已完成章节数量" className="mt-1 text-2xl font-semibold tabular-nums">{formatCountPair(counts.chapters)}</dd>
          </div>
          <div>
            <dt className="text-sm text-label-secondary">本次读取的任务点</dt>
            <dd aria-label="已完成任务数量" className="mt-1 text-2xl font-semibold tabular-nums">{formatCountPair(counts.tasks)}</dd>
          </div>
        </dl>
        <div className="border-t border-separator px-5 py-3">
          <TaskProgress progress={counts.courses.completed} total={counts.courses.total} label="课程完成进度" showCount={false} />
          <p className="mt-2 text-pretty text-xs leading-5 text-label-secondary">数量均为已完成 / 总数。已完成章节不再读取任务点，因此任务点数量仅包含本次读取的部分。</p>
        </div>
      </section>

      {unfinished.length > 0 ? (
        <section className="mt-5 overflow-hidden rounded-lg border border-separator bg-surface" aria-labelledby="task-issues-title">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-separator px-5 py-3">
            <h2 id="task-issues-title" className="text-balance font-semibold">需要处理的事项</h2>
            <span className="text-xs tabular-nums text-label-secondary">{unfinished.length} 门课程{unfinishedChapters > 0 ? ` · ${unfinishedChapters} 个章节` : ''}</span>
          </div>
          <UnfinishedList groups={unfinished} />
        </section>
      ) : terminalState && snapshot.state !== 'completed' ? (
        <Alert className="mt-5" variant="warning">{detailsError ? '章节详情暂时未能加载，正在重试。' : '当前记录未提供可定位的未完成章节。请查看运行日志，或返回课程启动重新读取课程。'}</Alert>
      ) : null}

      {!terminalState && jobs.length > 0 ? (
        <section className="mt-5 rounded-lg border border-separator bg-surface" aria-labelledby="task-jobs-title">
          <div className="flex items-center justify-between border-b border-separator px-5 py-3">
            <h2 id="task-jobs-title" className="text-balance font-semibold">正在处理</h2>
            <span className="text-xs tabular-nums text-label-secondary">{jobs.length} 项</span>
          </div>
          <div className="divide-y divide-separator">
            {jobs.map(([key, job]) => (
              <div key={key} className="space-y-2 px-5 py-4">
                {job.course || job.chapter ? <p className="text-pretty text-xs text-label-secondary">{[job.course, job.chapter].filter(Boolean).join(' · ')}</p> : null}
                {job.progress !== undefined ? (
                  <TaskProgress value={job.progress} label={jobName(job, key)} showCount={false} />
                ) : <p className="text-pretty text-sm font-medium">{jobName(job, key)}</p>}
                {job.current_time !== undefined || job.currentTime !== undefined || job.duration !== undefined ? (
                  <p className="text-right text-xs tabular-nums text-label-secondary">{formatDuration(job.current_time ?? job.currentTime)} / {formatDuration(job.duration)}</p>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <AnswerReport key={taskId} report={details.answer_report} stats={{ ...snapshot.stats, ...details.counts }} active={ACTIVE_STATES.has(snapshot.state)} pending={refreshingGrades} error={gradesError} onRefresh={handleRefreshGrades} />

      <section className="mt-5 rounded-lg border border-separator bg-surface" aria-labelledby="task-courses-title">
        <div className="border-b border-separator px-4 py-3">
          <h2 id="task-courses-title" className="text-balance font-semibold">全部课程</h2>
          <p className="mt-0.5 text-xs text-label-secondary">右侧显示已完成章节 / 总章节，展开可查看每章状态。</p>
        </div>
        {details.truncated ? (
          <Alert className="m-4" variant="warning">
            这条历史任务的部分课程详情已被旧版本截断，无法从历史记录恢复；新任务会完整保留课程详情。
          </Alert>
        ) : null}
        <TaskCourseList courses={details.courses} expanded={expandedCourses} onToggle={toggleCourse} />
      </section>

      <section className="mt-5 rounded-lg border border-separator bg-surface" aria-labelledby="task-logs-title">
        <div className="border-b border-separator px-4 py-3">
          <h2 id="task-logs-title" className="text-balance font-semibold">运行日志</h2>
          <p className="mt-0.5 text-pretty text-xs text-label-secondary">{snapshot.error ? <>本次记录的结束原因：<span>{snapshot.error}</span></> : '保留本次运行过程，供排查具体问题。'}</p>
        </div>
        {logs.some((entry) => entry.message === '历史敏感日志已清理') ? (
          <Alert className="m-4" variant="warning">
            这条任务的部分历史日志已被旧版本清理，原文无法恢复；新日志会保留进度和失败原因。
          </Alert>
        ) : null}
        <TaskLog items={logs} />
      </section>

      <p className="mt-4 break-all text-xs text-label-tertiary">运行编号：{snapshot.id || taskId}</p>

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
  KNOWN_TASK_STATES,
  normalizeDetails,
  normalizeLogPage,
  normalizeSnapshot,
  safeErrorMessage,
}
export default TaskPage
