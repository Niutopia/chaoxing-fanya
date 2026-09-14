import React, { useLayoutEffect, useRef } from 'react'
import { cn } from '../../lib/utils'

function timestampValue(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  return number > 100000000000 ? number : number * 1000
}

function formatTimestamp(value) {
  const milliseconds = timestampValue(value)
  if (milliseconds === null) return '—'
  const date = new Date(milliseconds)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function safeLevel(value) {
  const level = String(value ?? 'info').trim()
  return level ? level : 'info'
}

function safeMessage(value) {
  if (value === null || value === undefined) return ''
  return String(value)
    .replace(/\b(?:bearer\s+)[^\s,;}]+/gi, 'Bearer [redacted]')
    .replace(/((?:password|passwd|pass|api[-_]?key|access[-_]?token|refresh[-_]?token|token|secret|authori[sz]ation|cookie|cookies|key))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^,;&\s}]+)/gi, '$1=[redacted]')
}

/** A selectable, cursor-fed log stream that respects a user's scroll position. */
function TaskLog({ items = [], className }) {
  const logRef = useRef(null)
  const previousHeightRef = useRef(0)

  useLayoutEffect(() => {
    const node = logRef.current
    if (!node) return
    const wasNearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 32
    if (wasNearBottom && previousHeightRef.current > 0) {
      node.scrollTop = node.scrollHeight
    }
    previousHeightRef.current = node.scrollHeight
  }, [items.length])

  return (
    <div
      ref={logRef}
      role="list"
      aria-label="任务日志"
      tabIndex={0}
      className={cn(
        'max-h-72 overflow-auto border-y border-separator bg-black/[0.018] font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-accent-blue',
        className,
      )}
    >
      {items.length === 0 ? (
        <p className="px-3 py-5 font-sans text-sm text-label-secondary">暂无日志</p>
      ) : (
        <ol className="divide-y divide-separator/70">
          {items.map((item, index) => {
            const sequence = item?.sequence ?? index
            const level = safeLevel(item?.level)
            return (
              <li key={`${sequence}-${index}`} role="listitem" className="grid grid-cols-[auto_auto_minmax(0,1fr)] gap-2 px-3 py-2">
                <time className="select-text whitespace-nowrap text-label-tertiary" dateTime={String(item?.timestamp ?? '')}>
                  {formatTimestamp(item?.timestamp)}
                </time>
                <span className="select-text uppercase text-label-tertiary">{level}</span>
                <span className="select-text whitespace-pre-wrap break-words text-label-primary">
                  {safeMessage(item?.message)}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

export { formatTimestamp, safeMessage, TaskLog }
export default TaskLog
