import React from 'react'
import { cn } from '../../lib/utils'

function clamp(value, min, max) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return min
  return Math.min(max, Math.max(min, numeric))
}

const Progress = React.forwardRef(function Progress(
  {
    value = 0,
    max = 100,
    label,
    showValue = false,
    className,
    trackClassName,
    ...props
  },
  ref,
) {
  const safeMax = Number.isFinite(Number(max)) && Number(max) > 0 ? Number(max) : 100
  const safeValue = clamp(value, 0, safeMax)
  const percentage = Math.round((safeValue / safeMax) * 100)
  const accessibleLabel = label || '进度'

  return (
    <div ref={ref} className={cn('space-y-1.5', className)} {...props}>
      {label || showValue ? (
        <div className="flex items-center justify-between gap-3 text-xs text-label-secondary">
          {label ? <span className="truncate">{label}</span> : <span />}
          {showValue ? <span className="tabular-nums text-label-primary">{percentage}%</span> : null}
        </div>
      ) : null}
      <div
        className={cn('h-1.5 w-full overflow-hidden rounded-full bg-black/[0.08]', trackClassName)}
        role="progressbar"
        aria-label={accessibleLabel}
        aria-valuemin={0}
        aria-valuemax={safeMax}
        aria-valuenow={safeValue}
      >
        <div className="h-full rounded-full bg-accent-blue" style={{ width: `${percentage}%` }} />
      </div>
    </div>
  )
})

Progress.displayName = 'Progress'

export { Progress }
export default Progress
