import React from 'react'
import { cn } from '../../lib/utils'

const Input = React.forwardRef(function Input(
  { className, type = 'text', 'aria-invalid': ariaInvalid, ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      aria-invalid={ariaInvalid}
      className={cn(
        'flex h-9 w-full rounded-md border border-separator bg-surface px-2.5 py-1.5 text-sm text-label-primary shadow-none outline-none placeholder:text-label-tertiary focus-visible:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue/20 disabled:cursor-not-allowed disabled:opacity-55 file:border-0 file:bg-transparent file:text-sm file:font-medium',
        ariaInvalid && 'border-danger focus-visible:border-danger focus-visible:ring-danger/20',
        className,
      )}
      {...props}
    />
  )
})

Input.displayName = 'Input'

export default Input
