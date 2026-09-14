import React, { cloneElement, isValidElement, useId } from 'react'
import { cn } from '../../lib/utils'

const Field = React.forwardRef(function Field(
  {
    label,
    htmlFor,
    description,
    hint,
    error,
    required = false,
    children,
    className,
    ...props
  },
  ref,
) {
  const generatedId = useId()
  const inputId = htmlFor || `${generatedId}-input`
  const descriptionId = description || hint ? `${inputId}-description` : undefined
  const errorId = error ? `${inputId}-error` : undefined
  const describedBy = [descriptionId, errorId].filter(Boolean).join(' ') || undefined

  let control = children
  if (isValidElement(children)) {
    const existingDescribedBy = children.props['aria-describedby']
    const mergedDescribedBy = [existingDescribedBy, describedBy]
      .filter(Boolean)
      .join(' ') || undefined
    control = cloneElement(children, {
      id: children.props.id || inputId,
      'aria-describedby': mergedDescribedBy,
      'aria-invalid': error ? true : children.props['aria-invalid'],
    })
  }

  return (
    <div ref={ref} className={cn('space-y-1.5', className)} {...props}>
      {label ? (
        <label htmlFor={inputId} className="text-sm font-medium text-label-primary">
          {label}
          {required ? <span className="ml-1 text-danger" aria-hidden="true">*</span> : null}
        </label>
      ) : null}
      {description || hint ? (
        <p id={descriptionId} className="text-xs leading-4 text-label-secondary">{description || hint}</p>
      ) : null}
      {control}
      {error ? <p id={errorId} className="text-xs leading-4 text-danger" role="alert">{error}</p> : null}
    </div>
  )
})

Field.displayName = 'Field'

export { Field }
export default Field
