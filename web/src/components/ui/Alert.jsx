import React from 'react'
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react'
import { cn } from '../../lib/utils'

const variantStyles = {
  info: {
    container: 'border-accent-blue/30 bg-accent-blue/[0.06] text-label-primary',
    icon: Info,
    iconClass: 'text-accent-blue',
  },
  success: {
    container: 'border-success/30 bg-success/[0.06] text-label-primary',
    icon: CheckCircle2,
    iconClass: 'text-success',
  },
  warning: {
    container: 'border-warning/35 bg-warning/[0.08] text-label-primary',
    icon: TriangleAlert,
    iconClass: 'text-warning',
  },
  danger: {
    container: 'border-danger/30 bg-danger/[0.06] text-label-primary',
    icon: AlertCircle,
    iconClass: 'text-danger',
  },
  error: {
    container: 'border-danger/30 bg-danger/[0.06] text-label-primary',
    icon: AlertCircle,
    iconClass: 'text-danger',
  },
}

const Alert = React.forwardRef(function Alert(
  {
    variant = 'info',
    title,
    children,
    onDismiss,
    className,
    role = variant === 'danger' || variant === 'error' ? 'alert' : 'status',
    ...props
  },
  ref,
) {
  const styles = variantStyles[variant] ?? variantStyles.info
  const Icon = styles.icon

  return (
    <div
      ref={ref}
      role={role}
      className={cn(
        'flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm',
        styles.container,
        className,
      )}
      {...props}
    >
      <Icon aria-hidden="true" className={cn('mt-0.5 shrink-0', styles.iconClass)} size={16} strokeWidth={1.8} />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-medium text-label-primary">{title}</p> : null}
        {children ? <div className={cn(title && 'mt-0.5', 'leading-5 text-label-secondary')}>{children}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          className="inline-flex size-6 shrink-0 items-center justify-center rounded text-label-tertiary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          onClick={onDismiss}
          aria-label="关闭提示"
        >
          <X aria-hidden="true" size={14} strokeWidth={1.8} />
        </button>
      ) : null}
    </div>
  )
})

Alert.displayName = 'Alert'

export { Alert }
export default Alert
