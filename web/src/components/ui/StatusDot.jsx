import React from 'react'
import { cn } from '../../lib/utils'

const STATE_LABELS = {
  idle: '空闲',
  running: '运行中',
  stopping: '停止中',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
  success: '正常',
  warning: '需注意',
  error: '异常',
}

const STATE_COLORS = {
  idle: 'bg-label-tertiary',
  running: 'bg-accent-blue',
  stopping: 'bg-warning',
  completed: 'bg-success',
  failed: 'bg-danger',
  stopped: 'bg-label-tertiary',
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-danger',
}

export function taskStateLabel(state) {
  return STATE_LABELS[state] ?? '未知状态'
}

const StatusDot = React.forwardRef(function StatusDot(
  { status = 'idle', state, label, size = 'sm', className, ...props },
  ref,
) {
  const resolvedStatus = state ?? status
  const accessibleLabel = label || taskStateLabel(resolvedStatus)
  const sizeClass = size === 'lg' ? 'size-2.5' : size === 'md' ? 'size-2' : 'size-1.5'

  return (
    <span
      ref={ref}
      role="status"
      aria-label={accessibleLabel}
      title={accessibleLabel}
      data-status={resolvedStatus}
      className={cn('inline-flex shrink-0 items-center', className)}
      {...props}
    >
      <span aria-hidden="true" className={cn('rounded-full', sizeClass, STATE_COLORS[resolvedStatus] ?? STATE_COLORS.idle)} />
    </span>
  )
})

StatusDot.displayName = 'StatusDot'

export { StatusDot }
export default StatusDot
