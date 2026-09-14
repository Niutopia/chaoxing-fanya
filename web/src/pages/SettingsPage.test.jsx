import { render, screen } from '@testing-library/react'
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

const { getPreferences, savePreferences } = vi.hoisted(() => ({
  getPreferences: vi.fn(),
  savePreferences: vi.fn(),
}))

vi.mock('../api/accounts', () => ({
  getPreferences,
  savePreferences,
}))

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
  })
  getRuntimeSettings.mockResolvedValue({
    max_concurrent_accounts: 3,
    global_answer_concurrency: 4,
    request_timeout: 30,
    retry_count: 2,
  })
  saveAnswerConnection.mockResolvedValue({})
  saveRuntimeSettings.mockResolvedValue({})
  clearAnswerKey.mockResolvedValue({})
  testAnswerConnection.mockResolvedValue({ ok: true, model_found: true })
  getPreferences.mockResolvedValue({ notification_config: {}, ocr_config: {} })
  savePreferences.mockResolvedValue({})
})

test('shows a mask and does not render the saved api key', async () => {
  renderPage()

  expect(await screen.findByText('sk-••••••')).toBeInTheDocument()
  expect(screen.getByLabelText('替换 API Key')).toHaveValue('')
  expect(screen.queryByDisplayValue('saved-secret')).not.toBeInTheDocument()
})

test('tests the edited connection and preserves a saved key when replacement is blank', async () => {
  const user = userEvent.setup()
  testAnswerConnection.mockResolvedValue({ ok: true, model_found: true })
  renderPage()

  await user.clear(await screen.findByLabelText('基础地址'))
  await user.type(screen.getByLabelText('基础地址'), 'http://localhost:8849/v1')
  await user.click(screen.getByRole('button', { name: '测试连接' }))
  expect(await screen.findByText('连接成功，模型可用')).toBeInTheDocument()
  expect(testAnswerConnection).toHaveBeenCalledWith(expect.objectContaining({
    base_url: 'http://localhost:8849/v1',
    model: 'gemini-3.8-flash-high',
  }))
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

test.each([
  ['最大同时运行账户数', '0', '请输入 1 到 10'],
  ['全局答题并发数', '0', '请输入大于 0 的整数'],
  ['请求超时', '-1', '请输入大于 0 的秒数'],
])('validates %s', async (label, value, message) => {
  const user = userEvent.setup()
  renderPage()
  await user.clear(await screen.findByLabelText(label))
  await user.type(screen.getByLabelText(label), value)
  await user.click(screen.getByRole('button', { name: '保存设置' }))
  expect(await screen.findByText(message)).toBeInTheDocument()
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
  expect(saveAnswerConnection).toHaveBeenCalledWith(expect.objectContaining({ api_key: 'new-secret' }))
  expect(key).toHaveValue('')
})

test('does not render notification or OCR secrets and uses endpoint for OCR drafts', async () => {
  const user = userEvent.setup()
  const notificationUrl = 'https://notify.example.invalid/private-url'
  const notificationToken = 'notification-settings-secret'
  const chatId = 'tg-chat-settings-secret'
  const ocrKey = 'ocr-settings-secret'
  const nestedNotificationSecret = 'nested-notification-authorization-secret'
  const nestedOcrSecret = 'nested-ocr-secret'
  getPreferences.mockResolvedValue({
    notification_config: {
      enabled: true,
      provider: 'telegram',
      url: notificationUrl,
      token: notificationToken,
      tg_chat_id: chatId,
      has_token: true,
      has_url: true,
      has_tg_chat_id: true,
      nested: { authorization: nestedNotificationSecret },
    },
    ocr_config: {
      enabled: true,
      provider: 'openai',
      endpoint: 'http://ocr.example.invalid/v1',
      api_key: ocrKey,
      has_api_key: true,
      nested: { key: nestedOcrSecret, secret: 'nested-ocr-secret-2' },
    },
  })

  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SettingsPage accountId="account-a" accounts={[{ id: 'account-a', name: '账号 A' }]} />
    </MemoryRouter>,
  )

  expect(await screen.findByText('账号 A')).toBeInTheDocument()
  expect(document.body.textContent).not.toContain(notificationUrl)
  expect(document.body.textContent).not.toContain(notificationToken)
  expect(document.body.textContent).not.toContain(chatId)
  expect(document.body.textContent).not.toContain(ocrKey)
  expect(document.body.textContent).not.toContain(nestedNotificationSecret)
  expect(document.body.textContent).not.toContain(nestedOcrSecret)

  await user.click(screen.getByText('通知'))
  expect(screen.getByLabelText('通知地址')).toHaveValue('')
  expect(screen.getByLabelText('通知 Token')).toHaveValue('')
  expect(screen.getByLabelText('替换 Telegram Chat ID')).toHaveValue('')

  await user.click(screen.getByText('OCR'))
  expect(screen.getByLabelText('OCR 地址')).toHaveValue('http://ocr.example.invalid/v1')
  const replacement = screen.getByLabelText('替换 OCR API Key')
  expect(replacement).toHaveValue('')
  await user.type(replacement, 'new-ocr-secret')
  await user.click(screen.getByRole('button', { name: '保存设置' }))

  expect(savePreferences).toHaveBeenCalledWith('account-a', expect.objectContaining({
    ocr_config: expect.objectContaining({ endpoint: 'http://ocr.example.invalid/v1', api_key: 'new-ocr-secret' }),
  }))
  const payloadText = JSON.stringify(savePreferences.mock.calls[0][1])
  expect(payloadText).not.toContain(notificationUrl)
  expect(payloadText).not.toContain(notificationToken)
  expect(payloadText).not.toContain(chatId)
  expect(payloadText).not.toContain(ocrKey)
  expect(payloadText).not.toContain(nestedNotificationSecret)
  expect(payloadText).not.toContain(nestedOcrSecret)
  expect(payloadText).not.toContain('nested-ocr-secret-2')
})
