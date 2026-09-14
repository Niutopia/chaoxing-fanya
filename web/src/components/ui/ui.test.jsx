import { render, screen } from '@testing-library/react'
import Field from './Field'
import Input from './Input'

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
