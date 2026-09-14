import React from 'react'
import { cn } from '../../lib/utils'

const Button = React.forwardRef(function Button(
  {
    className,
    variant = 'default',
    size = 'default',
    loading = false,
    children,
    type = 'button',
    disabled,
    ...props
  },
  ref,
) {
  const variantStyles = {
    default: 'bg-accent-blue text-white hover:bg-accent-blue/90',
    primary: 'bg-accent-blue text-white hover:bg-accent-blue/90',
    destructive: 'bg-danger text-white hover:bg-danger/90',
    outline: 'border border-separator bg-surface text-label-primary hover:bg-black/[0.04]',
    secondary: 'bg-black/[0.06] text-label-primary hover:bg-black/[0.1]',
    ghost: 'text-label-secondary hover:bg-black/[0.06] hover:text-label-primary',
    link: 'h-auto px-0 text-accent-blue underline-offset-2 hover:underline',
  }

  const sizeStyles = {
    default: 'h-9 px-3.5',
    sm: 'h-8 px-2.5 text-[13px]',
    lg: 'h-10 px-4',
    icon: 'size-9',
  }

  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none',
        variantStyles[variant] ?? variantStyles.default,
        sizeStyles[size] ?? sizeStyles.default,
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <span className="mr-2 size-3 rounded-full border-2 border-current border-r-transparent" aria-hidden="true" /> : null}
      {children}
    </button>
  )
})

Button.displayName = 'Button'

export default Button
