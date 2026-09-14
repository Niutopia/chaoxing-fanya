import { ApiError, apiRequest, toApiError } from './client'

test('unwraps a direct success envelope', async () => {
  await expect(apiRequest(Promise.resolve({ status: true, data: { ok: true } }))).resolves.toEqual({ ok: true })
})

test('does not treat a direct envelope payload status as transport status', async () => {
  await expect(
    apiRequest(Promise.resolve({ status: true, data: { status: false, value: 1 } })),
  ).resolves.toEqual({ status: false, value: 1 })
})

test('preserves API error status, code, and message', async () => {
  const error = toApiError({
    response: {
      status: 409,
      data: { status: false, code: 'account_active', msg: '账户正在运行' },
    },
  })

  expect(error).toBeInstanceOf(ApiError)
  expect(error.status).toBe(409)
  expect(error.code).toBe('account_active')
  expect(error.message).toBe('账户正在运行')
})

test('rejects a direct error envelope', async () => {
  await expect(
    apiRequest(Promise.resolve({ status: false, code: 'not_found', msg: '不存在' })),
  ).rejects.toMatchObject({
    code: 'not_found',
    message: '不存在',
  })
})

test('supports positional ApiError metadata for page-level conflict handling', () => {
  const error = new ApiError('账户正在运行', 409, 'account_active')

  expect(error.status).toBe(409)
  expect(error.code).toBe('account_active')
})

test('falls back to a useful message when an API error message is blank', () => {
  const error = toApiError({
    response: { status: 500, data: { status: false, msg: '  ' } },
  })

  expect(error.message).toBe('请求失败，请稍后重试')
})
