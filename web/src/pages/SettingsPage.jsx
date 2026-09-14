import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { getPreferences, savePreferences } from '../api/accounts'
import {
  clearAnswerKey,
  getAnswerConnection,
  getRuntimeSettings,
  saveAnswerConnection,
  saveRuntimeSettings,
  testAnswerConnection,
} from '../api/settings'
import Alert from '../components/ui/Alert'
import Button from '../components/ui/Button'
import Field from '../components/ui/Field'
import Input from '../components/ui/Input'
import { cn } from '../lib/utils'

const DEFAULT_CONNECTION = {
  enabled: false,
  base_url: 'http://localhost:8849/v1',
  model: 'gemini-3.8-flash-high',
  has_api_key: false,
  api_key_mask: null,
  timeout_seconds: 30,
  max_retries: 3,
  max_concurrency: 4,
}

const DEFAULT_RUNTIME = {
  max_active_accounts: 3,
}

const SECRET_KEY_PATTERN = /(?:api[-_]?key|token|secret|password|credential|authorization|cookie|private[-_]?key)/i

function unwrap(value, keys = []) {
  if (value && typeof value === 'object') {
    for (const key of keys) {
      if (value[key] !== undefined) return value[key]
    }
    if (value.data !== undefined) return value.data
  }
  return value
}

function errorMessage(error, fallback) {
  const message = error?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

function numberValue(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function stringValue(value, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value)
}

function normalizeConnection(value) {
  const source = unwrap(value, ['connection', 'answer_connection'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { ...DEFAULT_CONNECTION }
  }
  return {
    ...DEFAULT_CONNECTION,
    ...source,
    enabled: source.enabled === true,
    base_url: stringValue(source.base_url, DEFAULT_CONNECTION.base_url),
    model: stringValue(source.model, DEFAULT_CONNECTION.model),
    timeout_seconds: numberValue(source.timeout_seconds, DEFAULT_CONNECTION.timeout_seconds),
    max_retries: numberValue(source.max_retries, DEFAULT_CONNECTION.max_retries),
    max_concurrency: numberValue(source.max_concurrency, DEFAULT_CONNECTION.max_concurrency),
  }
}

function normalizeRuntime(value) {
  const source = unwrap(value, ['runtime', 'settings'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { ...DEFAULT_RUNTIME }
  }
  return {
    ...DEFAULT_RUNTIME,
    ...source,
    max_active_accounts: numberValue(
      source.max_active_accounts ?? source.max_concurrent_accounts,
      DEFAULT_RUNTIME.max_active_accounts,
    ),
  }
}

/** Keep editable account configuration free of values that may be secrets. */
function safeConfig(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.entries(value).reduce((result, [key, item]) => {
    if (SECRET_KEY_PATTERN.test(key)) {
      if (item != null && String(item).trim()) result[`${key}_configured`] = true
      return result
    }
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      result[key] = safeConfig(item)
    } else if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
      result[key] = item
    }
    return result
  }, {})
}

function configuredSecret(value, names = []) {
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, item]) => {
    if (!names.length || names.some((name) => key.toLowerCase().includes(name))) {
      return SECRET_KEY_PATTERN.test(key) && item != null && String(item).trim()
    }
    return false
  })
}

function answerPayload(connection, apiKey) {
  const payload = {
    enabled: Boolean(connection.enabled),
    base_url: stringValue(connection.base_url).trim(),
    model: stringValue(connection.model).trim(),
    timeout_seconds: numberValue(connection.timeout_seconds, DEFAULT_CONNECTION.timeout_seconds),
    max_retries: Math.trunc(numberValue(connection.max_retries, DEFAULT_CONNECTION.max_retries)),
    max_concurrency: Math.trunc(numberValue(connection.max_concurrency, DEFAULT_CONNECTION.max_concurrency)),
  }
  const replacement = typeof apiKey === 'string' ? apiKey.trim() : ''
  if (replacement) payload.api_key = replacement
  return payload
}

function validateConnection(connection) {
  if (!stringValue(connection.base_url).trim()) return '请输入基础地址'
  if (!stringValue(connection.model).trim()) return '请输入模型名称'
  if (!Number.isFinite(Number(connection.max_concurrency)) || Number(connection.max_concurrency) <= 0 || !Number.isInteger(Number(connection.max_concurrency))) {
    return '请输入大于 0 的整数'
  }
  if (!Number.isFinite(Number(connection.timeout_seconds)) || Number(connection.timeout_seconds) <= 0) {
    return '请输入大于 0 的秒数'
  }
  if (!Number.isFinite(Number(connection.max_retries)) || Number(connection.max_retries) < 0 || !Number.isInteger(Number(connection.max_retries))) {
    return '请输入 0 或更大的整数'
  }
  return ''
}

function validateRuntime(runtime, connection) {
  const maxAccounts = Number(runtime.max_active_accounts)
  if (!Number.isInteger(maxAccounts) || maxAccounts < 1 || maxAccounts > 10) return '请输入 1 到 10'
  const concurrency = Number(connection.max_concurrency)
  if (!Number.isInteger(concurrency) || concurrency <= 0) return '请输入大于 0 的整数'
  const timeout = Number(connection.timeout_seconds)
  if (!Number.isFinite(timeout) || timeout <= 0) return '请输入大于 0 的秒数'
  if (!Number.isInteger(Number(connection.max_retries)) || Number(connection.max_retries) < 0) return '请输入 0 或更大的整数'
  return ''
}

function preferenceConfigPayload(notification, ocr, originalNotification, originalOcr) {
  return {
    notification_config: { ...originalNotification, ...notification },
    ocr_config: { ...originalOcr, ...ocr },
  }
}

function SettingsPage({ accounts = [], accountId: explicitAccountId, className }) {
  const params = useParams()
  const [searchParams] = useSearchParams()
  const accountFromQuery = searchParams.get('account')
  const initialAccountId = explicitAccountId ?? params.accountId ?? accountFromQuery ?? accounts[0]?.id ?? ''
  const [selectedAccountId, setSelectedAccountId] = useState(initialAccountId)
  const [connection, setConnection] = useState({ ...DEFAULT_CONNECTION })
  const [apiKey, setApiKey] = useState('')
  const [runtime, setRuntime] = useState({ ...DEFAULT_RUNTIME })
  const [notification, setNotification] = useState({ enabled: false, provider: '', url: '', token: '' })
  const [ocr, setOcr] = useState({ enabled: false, provider: '', base_url: '', model: '', api_key: '' })
  const originalNotificationRef = useRef({})
  const originalOcrRef = useRef({})
  const requestId = useRef(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const [runtimeError, setRuntimeError] = useState('')
  const [accountError, setAccountError] = useState('')
  const [success, setSuccess] = useState('')
  const [testState, setTestState] = useState('idle')
  const [testMessage, setTestMessage] = useState('')
  const [savingConnection, setSavingConnection] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearingKey, setClearingKey] = useState(false)

  useEffect(() => {
    setSelectedAccountId((current) => {
      if (current && (current === explicitAccountId || accounts.some((account) => String(account.id) === String(current)))) return current
      return explicitAccountId ?? accountFromQuery ?? accounts[0]?.id ?? ''
    })
  }, [accountFromQuery, accounts, explicitAccountId])

  const loadSettings = useCallback(async () => {
    const currentRequest = ++requestId.current
    setLoading(true)
    setLoadError('')
    setConnectionError('')
    setRuntimeError('')
    setAccountError('')

    const requests = [getAnswerConnection(), getRuntimeSettings()]
    if (selectedAccountId) requests.push(getPreferences(selectedAccountId))
    const [connectionResult, runtimeResult, accountResult] = await Promise.allSettled(requests)
    if (currentRequest !== requestId.current) return

    const failures = []
    if (connectionResult.status === 'fulfilled') {
      setConnection(normalizeConnection(connectionResult.value))
    } else {
      failures.push(errorMessage(connectionResult.reason, '答题连接设置加载失败'))
    }
    if (runtimeResult.status === 'fulfilled') {
      setRuntime(normalizeRuntime(runtimeResult.value))
    } else {
      failures.push(errorMessage(runtimeResult.reason, '运行设置加载失败'))
    }
    if (selectedAccountId && accountResult) {
      if (accountResult.status === 'fulfilled') {
        const source = unwrap(accountResult.value, ['preferences']) || {}
        const notificationSource = safeConfig(source.notification_config)
        const ocrSource = safeConfig(source.ocr_config)
        originalNotificationRef.current = notificationSource
        originalOcrRef.current = ocrSource
        setNotification({
          enabled: notificationSource.enabled === true,
          provider: stringValue(notificationSource.provider),
          url: stringValue(notificationSource.url ?? notificationSource.endpoint),
          token: '',
        })
        setOcr({
          enabled: ocrSource.enabled === true,
          provider: stringValue(ocrSource.provider),
          base_url: stringValue(ocrSource.base_url ?? ocrSource.endpoint),
          model: stringValue(ocrSource.model),
          api_key: '',
        })
      } else {
        failures.push(errorMessage(accountResult.reason, '账户通知与 OCR 设置加载失败'))
      }
    }
    setLoadError(failures.join('；'))
    setLoading(false)
  }, [selectedAccountId])

  useEffect(() => {
    loadSettings()
    return () => {
      requestId.current += 1
    }
  }, [loadSettings])

  const selectedAccount = useMemo(
    () => accounts.find((account) => String(account.id) === String(selectedAccountId)),
    [accounts, selectedAccountId],
  )

  const updateConnection = (field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setConnection((current) => ({ ...current, [field]: value }))
    setConnectionError('')
    setRuntimeError('')
    setSuccess('')
  }

  const updateRuntime = (field) => (event) => {
    setRuntime((current) => ({ ...current, [field]: event.target.value }))
    setRuntimeError('')
    setSuccess('')
  }

  const updateAccountConfig = (setter, field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setter((current) => ({ ...current, [field]: value }))
    setAccountError('')
    setSuccess('')
  }

  const handleTestConnection = async () => {
    const validationError = validateConnection(connection)
    if (validationError) {
      setConnectionError(validationError)
      setTestMessage('')
      return
    }
    setTestState('testing')
    setTestMessage('')
    setConnectionError('')
    try {
      const result = unwrap(await testAnswerConnection(answerPayload(connection, apiKey)), ['result']) || {}
      if (result.ok === true && result.model_found !== false) {
        setTestState('success')
        setTestMessage('连接成功，模型可用')
        return
      }
      setTestState('error')
      setTestMessage(errorMessage(result, '连接失败'))
    } catch (error) {
      setTestState('error')
      setTestMessage(errorMessage(error, '连接失败'))
    }
  }

  const handleSaveConnection = async () => {
    const validationError = validateConnection(connection)
    if (validationError) {
      setConnectionError(validationError)
      setSuccess('')
      return
    }
    setSavingConnection(true)
    setConnectionError('')
    setSuccess('')
    try {
      const saved = await saveAnswerConnection(answerPayload(connection, apiKey))
      if (saved) setConnection((current) => ({ ...current, ...normalizeConnection(saved) }))
      setApiKey('')
      setSuccess('连接设置已保存')
    } catch (error) {
      setConnectionError(errorMessage(error, '连接设置保存失败，请重试'))
    } finally {
      setSavingConnection(false)
    }
  }

  const handleClearKey = async () => {
    if (!confirmingClear || clearingKey) return
    setClearingKey(true)
    setConnectionError('')
    try {
      const cleared = await clearAnswerKey()
      setConnection((current) => ({
        ...current,
        ...(cleared ? normalizeConnection(cleared) : {}),
        has_api_key: false,
        api_key_mask: null,
      }))
      setApiKey('')
      setConfirmingClear(false)
      setSuccess('API Key 已清除')
    } catch (error) {
      setConnectionError(errorMessage(error, 'API Key 清除失败，请重试'))
    } finally {
      setClearingKey(false)
    }
  }

  const handleSaveSettings = async () => {
    const validationError = validateRuntime(runtime, connection)
    if (validationError) {
      setRuntimeError(validationError)
      setSuccess('')
      return
    }
    const connectionErrorMessage = validateConnection(connection)
    if (connectionErrorMessage) {
      setConnectionError(connectionErrorMessage)
      setSuccess('')
      return
    }

    setSavingSettings(true)
    setRuntimeError('')
    setConnectionError('')
    setAccountError('')
    setSuccess('')
    try {
      await saveRuntimeSettings({ max_active_accounts: Number(runtime.max_active_accounts) })
      const savedConnection = await saveAnswerConnection(answerPayload(connection, apiKey))
      if (savedConnection) setConnection((current) => ({ ...current, ...normalizeConnection(savedConnection) }))
      if (selectedAccountId) {
        const accountPayload = preferenceConfigPayload(
          { ...notification, ...(notification.token.trim() ? { token: notification.token.trim() } : {}) },
          { ...ocr, ...(ocr.api_key.trim() ? { api_key: ocr.api_key.trim() } : {}) },
          originalNotificationRef.current,
          originalOcrRef.current,
        )
        await savePreferences(selectedAccountId, accountPayload)
        originalNotificationRef.current = safeConfig(accountPayload.notification_config)
        originalOcrRef.current = safeConfig(accountPayload.ocr_config)
        setNotification((current) => ({ ...current, token: '' }))
        setOcr((current) => ({ ...current, api_key: '' }))
      }
      setApiKey('')
      setSuccess('设置已保存')
    } catch (error) {
      setRuntimeError(errorMessage(error, '设置保存失败，请重试'))
    } finally {
      setSavingSettings(false)
    }
  }

  if (loading) {
    return (
      <section className={cn('mx-auto w-full max-w-5xl px-4 py-8 md:px-8', className)} aria-labelledby="settings-title">
        <h1 id="settings-title" className="text-xl font-semibold tracking-tight">设置</h1>
        <p className="mt-2 text-sm text-label-secondary" role="status" aria-live="polite">正在加载设置…</p>
      </section>
    )
  }

  const connectionMask = connection.has_api_key
    ? connection.api_key_mask || 'Configured (••••)'
    : '未配置'

  return (
    <section className={cn('mx-auto w-full max-w-5xl px-4 py-7 md:px-8 md:py-8', className)} aria-labelledby="settings-title">
      <div>
        <p className="text-xs font-medium text-label-secondary">连接与运行</p>
        <h1 id="settings-title" className="mt-1 text-xl font-semibold tracking-tight">设置</h1>
        <p className="mt-1 text-sm text-label-secondary">共享答题服务和本地运行限制。</p>
      </div>

      {loadError ? <Alert className="mt-5" variant="warning" aria-live="polite">{loadError}</Alert> : null}
      {success ? <Alert className="mt-5" variant="success" aria-live="polite">{success}</Alert> : null}

      <div className="mt-7 space-y-0 border-y border-separator bg-surface">
        <details open className="group border-b border-separator">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>答题连接</span>
            <span aria-hidden="true" className="text-label-tertiary transition-transform group-open:rotate-180 motion-reduce:transition-none">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            {connectionError ? <Alert variant="danger" aria-live="polite">{connectionError}</Alert> : null}
            {testMessage ? (
              <Alert variant={testState === 'success' ? 'success' : 'danger'} aria-live="polite">{testMessage}</Alert>
            ) : null}

            <label className="touch-target flex min-h-11 cursor-pointer items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2"
                checked={Boolean(connection.enabled)}
                onChange={updateConnection('enabled')}
              />
              <span>
                <span className="block font-medium text-label-primary">启用答题连接</span>
                <span className="mt-0.5 block text-xs text-label-secondary">答题开启后，任务会使用这个共享服务。</span>
              </span>
            </label>

            <Field label="基础地址" htmlFor="answer-base-url" description="保留用户输入的地址格式，例如 http://localhost:8849/v1。">
              <Input id="answer-base-url" value={connection.base_url} onChange={updateConnection('base_url')} autoComplete="url" />
            </Field>
            <Field label="模型" htmlFor="answer-model">
              <Input id="answer-model" value={connection.model} onChange={updateConnection('model')} autoComplete="off" />
            </Field>
            <Field label="替换 API Key" htmlFor="replace-api-key" description="留空表示保留已保存的 Key；输入内容不会显示在页面状态中。">
              <Input
                id="replace-api-key"
                type="password"
                value={apiKey}
                onChange={(event) => {
                  setApiKey(event.target.value)
                  setConnectionError('')
                  setSuccess('')
                }}
                autoComplete="new-password"
                spellCheck="false"
              />
            </Field>
            <p className="text-xs text-label-secondary" role="status" aria-live="polite">
              当前 Key：<span className="font-medium text-label-primary">{connectionMask}</span>
            </p>

            <div className="grid gap-5 md:grid-cols-3">
              <Field label="请求超时" htmlFor="answer-timeout" description="秒">
                <Input id="answer-timeout" type="number" min="1" step="1" value={connection.timeout_seconds} onChange={updateConnection('timeout_seconds')} />
              </Field>
              <Field label="重试次数" htmlFor="answer-retries" description="可为 0">
                <Input id="answer-retries" type="number" min="0" step="1" value={connection.max_retries} onChange={updateConnection('max_retries')} />
              </Field>
              <Field label="全局答题并发数" htmlFor="answer-concurrency" description="大于 0 的整数">
                <Input id="answer-concurrency" type="number" min="1" step="1" value={connection.max_concurrency} onChange={updateConnection('max_concurrency')} />
              </Field>
            </div>

            <div className="flex flex-wrap justify-end gap-2 border-t border-separator pt-4">
              {connection.has_api_key ? (
                confirmingClear ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm text-label-secondary">
                    <span>确认清除已保存的 Key？</span>
                    <Button type="button" variant="outline" onClick={() => setConfirmingClear(false)}>取消</Button>
                    <Button type="button" variant="destructive" loading={clearingKey} onClick={handleClearKey}>确认清除</Button>
                  </div>
                ) : (
                  <Button type="button" variant="outline" onClick={() => setConfirmingClear(true)}>清除 API Key</Button>
                )
              ) : null}
              <Button type="button" variant="outline" loading={testState === 'testing'} onClick={handleTestConnection}>测试连接</Button>
              <Button type="button" onClick={handleSaveConnection} loading={savingConnection}>保存连接</Button>
            </div>
          </div>
        </details>

        <details open className="group border-b border-separator">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>运行限制</span>
            <span aria-hidden="true" className="text-label-tertiary transition-transform group-open:rotate-180 motion-reduce:transition-none">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            {runtimeError ? <Alert variant="danger" aria-live="polite">{runtimeError}</Alert> : null}
            <Field label="最大同时运行账户数" htmlFor="max-active-accounts" description="范围 1 到 10。">
              <Input id="max-active-accounts" type="number" min="1" max="10" step="1" value={runtime.max_active_accounts} onChange={updateRuntime('max_active_accounts')} />
            </Field>
            <p className="text-sm leading-5 text-label-secondary">全局答题并发数和请求超时在上方的答题连接中统一配置。</p>
            <div className="flex justify-end border-t border-separator pt-4">
              <Button type="button" loading={savingSettings} onClick={handleSaveSettings}>保存设置</Button>
            </div>
          </div>
        </details>

        <details className="group border-b border-separator">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>通知</span>
            <span aria-hidden="true" className="text-label-tertiary transition-transform group-open:rotate-180 motion-reduce:transition-none">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            {selectedAccountId ? (
              <p className="text-sm text-label-secondary">以下设置只作用于 {selectedAccount?.name || '当前账户'}。</p>
            ) : (
              <p className="text-sm text-label-secondary">从账户启动页进入设置后，可编辑对应账户的通知方式。</p>
            )}
            {accounts.length > 0 ? (
              <Field label="当前账户" htmlFor="settings-account">
                <select
                  id="settings-account"
                  value={selectedAccountId}
                  onChange={(event) => setSelectedAccountId(event.target.value)}
                  className="touch-target touch-target-compact flex h-9 w-full rounded-md border border-separator bg-surface px-2.5 py-1.5 text-sm text-label-primary outline-none focus-visible:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue/20"
                >
                  {accounts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </Field>
            ) : null}
            <label className="touch-target flex min-h-11 items-center gap-3 text-sm">
              <input type="checkbox" className="size-4 accent-accent-blue" checked={Boolean(notification.enabled)} onChange={updateAccountConfig(setNotification, 'enabled')} disabled={!selectedAccountId} />
              <span className="font-medium text-label-primary">启用通知</span>
            </label>
            <Field label="通知渠道" htmlFor="notification-provider">
              <Input id="notification-provider" value={notification.provider} onChange={updateAccountConfig(setNotification, 'provider')} disabled={!selectedAccountId} placeholder="例如：webhook" />
            </Field>
            <Field label="通知地址" htmlFor="notification-url">
              <Input id="notification-url" value={notification.url} onChange={updateAccountConfig(setNotification, 'url')} disabled={!selectedAccountId} placeholder="https://…" />
            </Field>
            <Field label="通知 Token" htmlFor="notification-token" description={configuredSecret(originalNotificationRef.current, ['token']) ? '已有 Token；留空表示保留' : '可选'}>
              <Input id="notification-token" type="password" value={notification.token} onChange={updateAccountConfig(setNotification, 'token')} disabled={!selectedAccountId} autoComplete="new-password" />
            </Field>
          </div>
        </details>

        <details className="group">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>OCR</span>
            <span aria-hidden="true" className="text-label-tertiary transition-transform group-open:rotate-180 motion-reduce:transition-none">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            <p className="text-sm text-label-secondary">OCR 设置只作用于当前账户；密钥输入框始终为空。</p>
            <label className="touch-target flex min-h-11 items-center gap-3 text-sm">
              <input type="checkbox" className="size-4 accent-accent-blue" checked={Boolean(ocr.enabled)} onChange={updateAccountConfig(setOcr, 'enabled')} disabled={!selectedAccountId} />
              <span className="font-medium text-label-primary">启用 OCR</span>
            </label>
            <Field label="OCR 提供方" htmlFor="ocr-provider">
              <Input id="ocr-provider" value={ocr.provider} onChange={updateAccountConfig(setOcr, 'provider')} disabled={!selectedAccountId} placeholder="例如：openai" />
            </Field>
            <Field label="OCR 地址" htmlFor="ocr-base-url">
              <Input id="ocr-base-url" value={ocr.base_url} onChange={updateAccountConfig(setOcr, 'base_url')} disabled={!selectedAccountId} placeholder="https://…" />
            </Field>
            <Field label="OCR 模型" htmlFor="ocr-model">
              <Input id="ocr-model" value={ocr.model} onChange={updateAccountConfig(setOcr, 'model')} disabled={!selectedAccountId} />
            </Field>
            <Field label="替换 OCR API Key" htmlFor="ocr-api-key" description={configuredSecret(originalOcrRef.current, ['api']) ? '已有 OCR Key；留空表示保留' : '可选'}>
              <Input id="ocr-api-key" type="password" value={ocr.api_key} onChange={updateAccountConfig(setOcr, 'api_key')} disabled={!selectedAccountId} autoComplete="new-password" />
            </Field>
          </div>
        </details>
      </div>
    </section>
  )
}

export {
  DEFAULT_CONNECTION,
  DEFAULT_RUNTIME,
  answerPayload,
  normalizeConnection,
  normalizeRuntime,
  safeConfig,
  validateConnection,
  validateRuntime,
}
export default SettingsPage
