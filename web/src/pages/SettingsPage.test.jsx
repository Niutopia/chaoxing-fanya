import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, vi } from 'vitest'
import { ApiError } from '../api/client'
import {
  clearAnswerKey,
  getAnswerConnection,
  getRuntimeSettings,
  saveAnswerConnection,
  saveRuntimeSettings,
  testAnswerConnection,
} from '../api/settings'
import SettingsPage from './SettingsPage'

vi.mock('../api/settings', () => ({
  clearAnswerKey: vi.fn(),
  getAnswerConnection: vi.fn(),
  getRuntimeSettings: vi.fn(),
  saveAnswerConnection: vi.fn(),
  saveRuntimeSettings: vi.fn(),
  testAnswerConnection: vi.fn(),
}))

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SettingsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  getAnswerConnection.mockResolvedValue({
    enabled: true,
    base_url: 'http://localhost:8849/v1',
    model: 'gemini-3.8-flash-high',
    has_api_key: true,
    api_key_mask: 'sk-••••••',
    last_test_status: 'success',
    timeout_seconds: 30,
    max_retries: 2,
    max_concurrency: 4,
  })
  getRuntimeSettings.mockResolvedValue({ max_active_accounts: 3 })
  saveAnswerConnection.mockResolvedValue({})
  saveRuntimeSettings.mockResolvedValue({})
  clearAnswerKey.mockResolvedValue({})
  testAnswerConnection.mockResolvedValue({ ok: true, model_found: true })
})

test('renders only global settings without account notification or OCR controls', async () => {
  renderPage()

  expect(await screen.findByText('所有账户共享这一答题服务。')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '全局设置' })).toBeInTheDocument()
  expect(screen.queryByText('通知')).not.toBeInTheDocument()
  expect(screen.queryByText('OCR')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('当前账户')).not.toBeInTheDocument()
})

test('shows a mask and never renders the saved api key', async () => {
  renderPage()

  expect(await screen.findByText('sk-••••••')).toBeInTheDocument()
  expect(screen.getByLabelText('替换 API Key')).toHaveValue('')
  expect(screen.queryByDisplayValue('saved-secret')).not.toBeInTheDocument()
})

test('restores the saved successful connection test when the page loads', async () => {
  renderPage()
  expect(await screen.findByText('当前连接已通过测试。')).toBeInTheDocument()
  expect(testAnswerConnection).not.toHaveBeenCalled()
})

test('preserves an acknowledged successful connection test after saving', async () => {
  saveAnswerConnection.mockResolvedValue({ last_test_status: 'success', has_api_key: true })
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole('button', { name: '保存连接' }))
  expect(await screen.findByText('连接设置已保存')).toBeInTheDocument()
  expect(screen.getByTestId('connection-test-status')).toHaveTextContent('当前连接已通过测试。')
})

test('does not show a load error when the connection request rejects with AbortError', async () => {
  const aborted = new Error('connection request cancelled')
  aborted.name = 'AbortError'
  getAnswerConnection.mockRejectedValue(aborted)

  renderPage()

  expect(await screen.findByText('所有账户共享这一答题服务。')).toBeInTheDocument()
  expect(screen.queryByText('connection request cancelled')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重新加载' })).not.toBeInTheDocument()
})

test('does not show a load error when the runtime request rejects with ERR_CANCELED', async () => {
  const cancelled = Object.assign(new Error('runtime request cancelled'), { code: 'ERR_CANCELED' })
  getRuntimeSettings.mockRejectedValue(cancelled)

  renderPage()

  expect(await screen.findByText('所有账户共享这一答题服务。')).toBeInTheDocument()
  expect(screen.queryByText('runtime request cancelled')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重新加载' })).not.toBeInTheDocument()
})

test('tests the edited connection and preserves a saved key when replacement is blank', async () => {
  const user = userEvent.setup()
  renderPage()

  await user.clear(await screen.findByLabelText('基础地址'))
  await user.type(screen.getByLabelText('基础地址'), 'http://localhost:8849/v1')
  await user.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接成功，模型可用')).toBeInTheDocument()
  expect(testAnswerConnection).toHaveBeenCalledWith(expect.objectContaining({
    base_url: 'http://localhost:8849/v1',
    model: 'gemini-3.8-flash-high',
  }), { signal: expect.any(AbortSignal) })

  await user.click(screen.getByRole('button', { name: '保存连接' }))
  expect(saveAnswerConnection.mock.calls[0][0]).not.toHaveProperty('api_key')
})

test('requires confirmation before clearing the saved key', async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole('button', { name: '清除 API Key' }))
  expect(clearAnswerKey).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '确认清除' }))
  expect(clearAnswerKey).toHaveBeenCalledTimes(1)
})

test('saves runtime limits independently from the answer connection', async () => {
  const user = userEvent.setup()
  renderPage()

  const maxAccounts = await screen.findByLabelText('最大同时运行账户数')
  await user.clear(maxAccounts)
  await user.type(maxAccounts, '5')
  await user.click(screen.getByRole('button', { name: '保存运行限制' }))

  expect(saveRuntimeSettings).toHaveBeenCalledWith(
    { max_active_accounts: 5 },
    { signal: expect.any(AbortSignal) },
  )
  expect(saveAnswerConnection).not.toHaveBeenCalled()
  expect(await screen.findByText('运行限制已保存')).toBeInTheDocument()
})

test('validates only the runtime field when saving runtime limits', async () => {
  const user = userEvent.setup()
  renderPage()

  await user.clear(await screen.findByLabelText('全局答题并发数'))
  await user.type(screen.getByLabelText('全局答题并发数'), '0')
  const maxAccounts = screen.getByLabelText('最大同时运行账户数')
  await user.clear(maxAccounts)
  await user.type(maxAccounts, '5')
  await user.click(screen.getByRole('button', { name: '保存运行限制' }))

  expect(saveRuntimeSettings).toHaveBeenCalledWith(
    { max_active_accounts: 5 },
    { signal: expect.any(AbortSignal) },
  )
})

test('rejects a runtime account limit outside 1 to 10', async () => {
  const user = userEvent.setup()
  renderPage()

  const input = await screen.findByLabelText('最大同时运行账户数')
  await user.clear(input)
  await user.type(input, '0')
  await user.click(screen.getByRole('button', { name: '保存运行限制' }))

  expect(await screen.findByText('请输入 1 到 10')).toBeInTheDocument()
  expect(saveRuntimeSettings).not.toHaveBeenCalled()
})

test.each([
  ['全局答题并发数', '0', '请输入大于 0 的整数'],
  ['请求超时', '-1', '请输入大于 0 的秒数'],
  ['重试次数', '-1', '请输入 0 或更大的整数'],
])('validates %s when saving the answer connection', async (label, value, message) => {
  const user = userEvent.setup()
  renderPage()

  const input = await screen.findByLabelText(label)
  await user.clear(input)
  await user.type(input, value)
  await user.click(screen.getByRole('button', { name: '保存连接' }))

  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(saveAnswerConnection).not.toHaveBeenCalled()
})

test('does not print a submitted api key when connection testing fails', async () => {
  const user = userEvent.setup()
  const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  testAnswerConnection.mockRejectedValue(new ApiError('连接失败', 503, 'answer_unavailable'))
  renderPage()

  await user.type(await screen.findByLabelText('替换 API Key'), 'local-test-secret')
  await user.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接失败')).toBeInTheDocument()
  expect(JSON.stringify(consoleSpy.mock.calls)).not.toContain('local-test-secret')
  consoleSpy.mockRestore()
})

test('clears the draft key after a successful connection save', async () => {
  const user = userEvent.setup()
  renderPage()

  const key = await screen.findByLabelText('替换 API Key')
  await user.type(key, 'new-secret')
  await user.click(screen.getByRole('button', { name: '保存连接' }))

  expect(saveAnswerConnection).toHaveBeenCalledWith(
    expect.objectContaining({ api_key: 'new-secret' }),
    { signal: expect.any(AbortSignal) },
  )
  expect(key).toHaveValue('')
})

test('clears runtime success before a pending key clear', async () => {
  const user = userEvent.setup()
  let resolveClear
  clearAnswerKey.mockImplementation(() => new Promise((resolve) => { resolveClear = resolve }))
  renderPage()

  await user.click(await screen.findByRole('button', { name: '保存运行限制' }))
  expect(await screen.findByText('运行限制已保存')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '清除 API Key' }))
  await user.click(screen.getByRole('button', { name: '确认清除' }))
  expect(screen.queryByText('运行限制已保存')).not.toBeInTheDocument()

  await act(async () => { resolveClear({ has_api_key: false, api_key_mask: null }) })
  expect(await screen.findByText('API Key 已清除')).toBeInTheDocument()
})

test('invalidates a successful connection test when a draft value changes', async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接成功，模型可用')).toBeInTheDocument()
  await user.clear(screen.getByLabelText('模型'))
  await user.type(screen.getByLabelText('模型'), 'another-model')

  expect(screen.queryByText('连接成功，模型可用')).not.toBeInTheDocument()
  expect(screen.getByTestId('connection-test-status')).toHaveTextContent('尚未测试当前连接')
})

test('does not let an in-flight test restore success after the draft changes', async () => {
  const user = userEvent.setup()
  let resolveProbe
  testAnswerConnection.mockImplementation(() => new Promise((resolve) => { resolveProbe = resolve }))
  renderPage()

  await user.click(await screen.findByRole('button', { name: '测试连接' }))
  await user.clear(screen.getByLabelText('模型'))
  await user.type(screen.getByLabelText('模型'), 'new-model')
  await act(async () => { resolveProbe({ ok: true, model_found: true }) })

  await waitFor(() => expect(screen.queryByText('连接成功，模型可用')).not.toBeInTheDocument())
})

test('locks connection controls while a save is pending and applies the submitted response', async () => {
  const user = userEvent.setup()
  let resolveSave
  saveAnswerConnection.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve }))
  renderPage()

  const model = await screen.findByLabelText('模型')
  await user.clear(model)
  await user.type(model, 'submitted-model')
  const key = screen.getByLabelText('替换 API Key')
  await user.type(key, 'submitted-secret')
  await user.click(screen.getByRole('button', { name: '保存连接' }))

  expect(saveAnswerConnection).toHaveBeenCalledWith(
    expect.objectContaining({ model: 'submitted-model', api_key: 'submitted-secret' }),
    { signal: expect.any(AbortSignal) },
  )
  expect(model).toBeDisabled()
  expect(screen.getByLabelText('基础地址')).toBeDisabled()
  expect(screen.getByLabelText('替换 API Key')).toBeDisabled()
  expect(screen.getByLabelText('请求超时')).toBeDisabled()
  expect(screen.getByLabelText('重试次数')).toBeDisabled()
  expect(screen.getByLabelText('全局答题并发数')).toBeDisabled()
  expect(screen.getByRole('button', { name: '清除 API Key' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '保存连接' })).toBeDisabled()
  expect(screen.getByLabelText('最大同时运行账户数')).not.toBeDisabled()

  await user.type(model, 'edited-during-save')
  expect(model).toHaveValue('submitted-model')
  await act(async () => { resolveSave({ model: 'submitted-model' }) })

  expect(await screen.findByText('连接设置已保存')).toBeInTheDocument()
  expect(model).toHaveValue('submitted-model')
  expect(key).toHaveValue('')
  expect(model).not.toBeDisabled()
})

test('disables connection testing throughout a connection mutation', async () => {
  const user = userEvent.setup()
  let resolveSave
  saveAnswerConnection.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve }))
  renderPage()

  await user.click(await screen.findByRole('button', { name: '保存连接' }))
  expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()
  await act(async () => { resolveSave({ has_api_key: true, api_key_mask: 'sk-••••••' }) })
  expect(await screen.findByRole('button', { name: '测试连接' })).not.toBeDisabled()
})

test('merges saved metadata without rendering a plaintext key from the response', async () => {
  const user = userEvent.setup()
  saveAnswerConnection.mockResolvedValue({
    has_api_key: true,
    api_key_mask: 'sk-••••••',
    api_key: 'response-plaintext-must-not-leak',
  })
  renderPage()

  await user.type(await screen.findByLabelText('替换 API Key'), 'replacement-secret')
  await user.click(screen.getByRole('button', { name: '保存连接' }))

  expect(await screen.findByText('连接设置已保存')).toBeInTheDocument()
  expect(document.body.textContent).not.toContain('response-plaintext-must-not-leak')
})

test('locks runtime controls while a save is pending and applies the submitted response', async () => {
  const user = userEvent.setup()
  let resolveSave
  saveRuntimeSettings.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve }))
  renderPage()

  const input = await screen.findByLabelText('最大同时运行账户数')
  await user.clear(input)
  await user.type(input, '4')
  await user.click(screen.getByRole('button', { name: '保存运行限制' }))

  expect(input).toBeDisabled()
  expect(screen.getByRole('button', { name: '保存运行限制' })).toBeDisabled()
  expect(screen.getByLabelText('模型')).not.toBeDisabled()
  await user.type(input, '6')
  expect(input).toHaveValue(4)
  await act(async () => { resolveSave({ max_active_accounts: 4 }) })

  expect(await screen.findByText('运行限制已保存')).toBeInTheDocument()
  expect(input).toHaveValue(4)
  expect(input).not.toBeDisabled()
})

test('keeps the runtime section usable while the connection save is pending', async () => {
  const user = userEvent.setup()
  let resolveConnection
  saveAnswerConnection.mockImplementation(() => new Promise((resolve) => { resolveConnection = resolve }))
  renderPage()

  await user.click(await screen.findByRole('button', { name: '保存连接' }))
  expect(screen.getByLabelText('最大同时运行账户数')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: '保存运行限制' })).not.toBeDisabled()

  await act(async () => { resolveConnection({}) })
})

test('keeps the connection section usable while the runtime save is pending', async () => {
  const user = userEvent.setup()
  let resolveRuntime
  saveRuntimeSettings.mockImplementation(() => new Promise((resolve) => { resolveRuntime = resolve }))
  renderPage()

  await user.click(await screen.findByRole('button', { name: '保存运行限制' }))
  expect(screen.getByLabelText('模型')).not.toBeDisabled()
  expect(screen.getByRole('button', { name: '保存连接' })).not.toBeDisabled()

  await act(async () => { resolveRuntime({}) })
})

test('ignores a connection save that resolves after unmount without reporting an error', async () => {
  const user = userEvent.setup()
  let resolveSave
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  saveAnswerConnection.mockImplementation((_payload, options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveSave = resolve })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '保存连接' }))
  unmount()
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveSave({ model: 'late-model' }) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})

test('handles a rejected connection save after unmount without an unhandled error', async () => {
  const user = userEvent.setup()
  let rejectSave
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  saveAnswerConnection.mockImplementation((_payload, options) => {
    signal = options?.signal
    return new Promise((_resolve, reject) => { rejectSave = reject })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '保存连接' }))
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { rejectSave(new Error('late connection failure')) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})

test('ignores a key clear that resolves after unmount without reporting an error', async () => {
  const user = userEvent.setup()
  let resolveClear
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  clearAnswerKey.mockImplementation((options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveClear = resolve })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '清除 API Key' }))
  await user.click(screen.getByRole('button', { name: '确认清除' }))
  unmount()
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveClear({ has_api_key: false }) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})

test('handles a rejected key clear after unmount without an unhandled error', async () => {
  const user = userEvent.setup()
  let rejectClear
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  clearAnswerKey.mockImplementation((options) => {
    signal = options?.signal
    return new Promise((_resolve, reject) => { rejectClear = reject })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '清除 API Key' }))
  await user.click(screen.getByRole('button', { name: '确认清除' }))
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { rejectClear(new Error('late clear failure')) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})

test('ignores a runtime save that resolves after unmount without reporting an error', async () => {
  const user = userEvent.setup()
  let resolveSave
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  saveRuntimeSettings.mockImplementation((_payload, options) => {
    signal = options?.signal
    return new Promise((resolve) => { resolveSave = resolve })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '保存运行限制' }))
  unmount()
  expect(signal).toBeInstanceOf(AbortSignal)
  expect(signal.aborted).toBe(true)
  await act(async () => { resolveSave({ max_active_accounts: 5 }) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})

test('handles a rejected runtime save after unmount without an unhandled error', async () => {
  const user = userEvent.setup()
  let rejectSave
  let signal
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  saveRuntimeSettings.mockImplementation((_payload, options) => {
    signal = options?.signal
    return new Promise((_resolve, reject) => { rejectSave = reject })
  })
  const { unmount } = renderPage()

  await user.click(await screen.findByRole('button', { name: '保存运行限制' }))
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { rejectSave(new Error('late runtime failure')) })

  expect(consoleError).not.toHaveBeenCalled()
  consoleError.mockRestore()
})
