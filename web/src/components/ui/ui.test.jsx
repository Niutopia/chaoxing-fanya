import { render, screen } from '@testing-library/react'
import Alert from './Alert'
import Button from './Button'
import Field from './Field'
import Input from './Input'

import '../../index.css'

test('Field preserves existing descriptions while linking its error message', () => {
  render(
    <Field label="账户名称" htmlFor="name" description="显示名称" error="请输入账户名称">
      <Input id="name" aria-describedby="custom-help" />
    </Field>,
  )

  expect(screen.getByLabelText('账户名称')).toHaveAttribute(
    'aria-describedby',
    expect.stringContaining('custom-help'),
  )
  expect(screen.getByLabelText('账户名称')).toHaveAttribute(
    'aria-describedby',
    expect.stringContaining('name-error'),
  )
  expect(screen.getByLabelText('账户名称')).toHaveAttribute('aria-invalid', 'true')
})

test('marks common controls as mobile-safe touch targets', () => {
  render(
    <>
      <Button>保存</Button>
      <Input aria-label="账户名称" />
      <Alert onDismiss={() => {}}>连接失败</Alert>
    </>,
  )

  expect(screen.getByRole('button', { name: '保存' })).toHaveClass('touch-target')
  expect(screen.getByLabelText('账户名称')).toHaveClass('touch-target')
  expect(screen.getByRole('button', { name: '关闭提示' })).toHaveClass('touch-target')
})

test('ships one reduced-motion foundation rule for transitions and animations', () => {
  const reducedMotionRule = [...document.styleSheets]
    .flatMap((sheet) => [...sheet.cssRules])
    .find((rule) => rule.conditionText === '(prefers-reduced-motion: reduce)')

  expect(reducedMotionRule).toBeDefined()
  const declarations = [...reducedMotionRule.cssRules]
    .find((rule) => rule.selectorText.includes('*'))
    ?.style
  expect(declarations?.getPropertyValue('transition')).toBe('none')
  expect(declarations?.getPropertyValue('animation')).toBe('none')
})
