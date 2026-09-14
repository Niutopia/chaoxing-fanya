import React from 'react'
import { cn } from '../../lib/utils'

function finiteNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function displayNumber(value) {
  if (!Number.isFinite(value)) return '0'
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

/**
 * Flat, accessible progress meter for task counts and percentage jobs.
 * ``value`` is a percentage; when ``total`` is supplied, ``progress`` and
 * ``total`` are displayed as a count and converted into a percentage.
 */
const TaskProgress = React.forwardRef(function TaskProgress(
  {
    value,
    progress,
    total,
    label = '任务进度',
    showCount = total !== undefined,
    className,
    ...props
  },
  ref,
) {
  const hasTotal = total !== undefined && total !== null
  const safeTotal = hasTotal ? Math.max(0, finiteNumber(total)) : 0
  const safeProgress = hasTotal
    ? clamp(finiteNumber(progress), 0, safeTotal)
    : clamp(finiteNumber(value ?? progress), 0, 100)
  const percentage = hasTotal
    ? safeTotal > 0 ? clamp((safeProgress / safeTotal) * 100, 0, 100) : 0
    : safeProgress
  const countText = hasTotal
    ? `${displayNumber(safeProgress)} / ${displayNumber(safeTotal)}`
    : `${Math.round(percentage)}%`

  return (
    <div ref={ref} className={cn('space-y-1.5', className)} {...props}>
      <div className="flex items-center justify-between gap-3 text-xs text-label-secondary">
        <span className="min-w-0 truncate">{label}</span>
        {showCount ? <span className="shrink-0 tabular-nums text-label-primary">{countText}</span> : null}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percentage}
        className="h-1.5 w-full overflow-hidden rounded-full bg-black/[0.08]"
      >
        <div
          className="h-full rounded-full bg-accent-blue"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
})

TaskProgress.displayName = 'TaskProgress'

export { clamp, TaskProgress }
export default TaskProgress
