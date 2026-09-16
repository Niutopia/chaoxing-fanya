import React from 'react'
import { cn } from '../../lib/utils'

const STATUS_LABELS = {
  idle: '空闲',
  running: '运行中',
  stopping: '正在停止',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
  pending: '待处理',
  not_open: '未开放',
  skipped: '待手动完成',
  blocked: '待条件满足',
}

const STATUS_COLORS = {
  idle: 'bg-label-tertiary',
  running: 'bg-accent-blue',
  stopping: 'bg-warning',
  completed: 'bg-success',
  failed: 'bg-danger',
  stopped: 'bg-label-tertiary',
  blocked: 'bg-warning',
  not_open: 'bg-warning',
  skipped: 'bg-warning',
}

const STATUS_TEXT_COLORS = {
  idle: 'text-label-secondary',
  running: 'text-accent-blue',
  stopping: 'text-label-primary',
  completed: 'text-label-primary',
  failed: 'text-danger',
  stopped: 'text-label-secondary',
}

export function taskStatusLabel(state) {
  return STATUS_LABELS[state] ?? '未知状态'
}

/** Render a task state with text and a semantic color cue. */
function TaskStatus({ state = 'idle', className, ...props }) {
  const label = taskStatusLabel(state)
  const dotColor = STATUS_COLORS[state] ?? STATUS_COLORS.idle
  const textColor = STATUS_TEXT_COLORS[state] ?? STATUS_TEXT_COLORS.idle

  return (
    <span
      role="status"
      aria-label={label}
      data-state={state}
      className={cn('inline-flex items-center gap-2 text-sm font-medium', textColor, className)}
      {...props}
    >
      <span aria-hidden="true" className={cn('size-2 rounded-full', dotColor)} />
      <span>{label}</span>
    </span>
  )
}

export { STATUS_LABELS, TaskStatus }
export default TaskStatus
