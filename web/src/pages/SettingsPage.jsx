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

function safeMask(value, configured) {
  if (!configured) return null
  if (typeof value === 'string' && /(?:•|\*{2,})/.test(value)) return value
  return 'Configured (••••)'
}

function normalizeConnection(value) {
  const source = unwrap(value, ['connection', 'answer_connection'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return { ...DEFAULT_CONNECTION }
  }
  return {
    // Keep the public connection shape explicit.  In particular, never
    // spread an API response into React state where an accidental api_key
    // field could survive a render or be copied into a later payload.
    enabled: source.enabled === true,
    base_url: stringValue(source.base_url, DEFAULT_CONNECTION.base_url),
    model: stringValue(source.model, DEFAULT_CONNECTION.model),
    has_api_key: source.has_api_key === true,
    api_key_mask: safeMask(source.api_key_mask, source.has_api_key === true),
    last_test_status: typeof source.last_test_status === 'string'
      ? source.last_test_status
      : 'untested',
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
    max_active_accounts: numberValue(
      source.max_active_accounts ?? source.max_concurrent_accounts,
      DEFAULT_RUNTIME.max_active_accounts,
    ),
  }
}

const CONFIG_MASK = 'Configured (••••)'

function configKey(key) {
  return String(key).trim().replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

function compactConfigKey(key) {
  return configKey(key).replace(/_/g, '')
}

function isConfigMetadataKey(key) {
  const normalized = configKey(key)
  return normalized.startsWith('has_')
    || normalized.endsWith('_mask')
    || normalized.endsWith('_configured')
}

function isSensitiveConfigKey(key, { notification = false } = {}) {
  const compact = compactConfigKey(key)
  if (!compact) return false
  if (
    compact === 'key'
    || ['apikey', 'accesstoken', 'accesskey', 'token', 'secret', 'authorization', 'password', 'credential', 'cookie', 'privatekey']
      .some((marker) => compact.includes(marker))
  ) return true
  return notification && ['url', 'uri', 'endpoint', 'webhook', 'chatid', 'chat'].some((marker) => compact.includes(marker))
}

function configValuePresent(value) {
  if (value == null) return false
  if (typeof value === 'string') return value.trim().length > 0
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return Boolean(value)
}

/** Keep editable account configuration free of values that may be secrets. */
function safeConfig(value, options = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.entries(value).reduce((result, [key, item]) => {
    if (isConfigMetadataKey(key)) {
      // Metadata is already safe and is useful for showing “configured” copy.
      const normalized = configKey(key)
      if (normalized.startsWith('has_')) {
        result[key] = item === true
      } else if (normalized.endsWith('_mask') || normalized.endsWith('_configured')) {
        result[key] = item ? CONFIG_MASK : null
      }
      return result
    }
    if (isSensitiveConfigKey(key, options)) {
      const normalized = configKey(key)
      const present = configValuePresent(item)
      result[`has_${normalized}`] = present
      result[`${normalized}_mask`] = present ? CONFIG_MASK : null
      return result
    }
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      result[key] = safeConfig(item, options)
    } else if (Array.isArray(item)) {
      result[key] = item.map((entry) => (
        entry && typeof entry === 'object' && !Array.isArray(entry)
          ? safeConfig(entry, options)
          : entry
      ))
    } else if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
      result[key] = item
    }
    return result
  }, {})
}

function configuredSecret(value, names = []) {
  if (!value || typeof value !== 'object') return false
  const wanted = names.map((name) => compactConfigKey(name))
  return Object.entries(value).some(([key, item]) => {
    const normalized = compactConfigKey(key)
    if (normalized.startsWith('has') && item === true) {
      const candidate = normalized.slice(3)
      return !wanted.length || wanted.some((name) => candidate.includes(name))
    }
    if (normalized.endsWith('configured') && item === true) {
      const candidate = normalized.slice(0, -10)
      return !wanted.length || wanted.some((name) => candidate.includes(name))
    }
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return configuredSecret(item, names)
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

function connectionDraftFingerprint(connection, apiKey) {
  return JSON.stringify({
    enabled: connection?.enabled === true,
    base_url: connection?.base_url == null ? '' : String(connection.base_url),
    model: connection?.model == null ? '' : String(connection.model),
    timeout_seconds: connection?.timeout_seconds == null ? '' : String(connection.timeout_seconds),
    max_retries: connection?.max_retries == null ? '' : String(connection.max_retries),
    max_concurrency: connection?.max_concurrency == null ? '' : String(connection.max_concurrency),
    api_key: typeof apiKey === 'string' ? apiKey : '',
  })
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

function editableConfig(value, options = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.entries(value).reduce((result, [key, item]) => {
    if (isConfigMetadataKey(key)) return result
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      result[key] = editableConfig(item, options)
    } else if (Array.isArray(item)) {
      result[key] = item.map((entry) => (
        entry && typeof entry === 'object' && !Array.isArray(entry)
          ? editableConfig(entry, options)
          : entry
      ))
    } else if (!isSensitiveConfigKey(key, options) && (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean')) {
      result[key] = item
    }
    return result
  }, {})
}

function preferenceConfigPayload(notification, ocr, originalNotification, originalOcr) {
  const notificationPayload = {
    ...editableConfig(originalNotification, { notification: true }),
    enabled: Boolean(notification.enabled),
    provider: stringValue(notification.provider).trim(),
  }
  const notificationUrl = stringValue(notification.url).trim()
  const notificationToken = stringValue(notification.token).trim()
  const notificationChatId = stringValue(notification.tg_chat_id).trim()
  if (notificationUrl) notificationPayload.url = notificationUrl
  if (notificationToken) notificationPayload.token = notificationToken
  if (notificationChatId) notificationPayload.tg_chat_id = notificationChatId

  const ocrPayload = {
    ...editableConfig(originalOcr),
    enabled: Boolean(ocr.enabled),
    provider: stringValue(ocr.provider).trim(),
    endpoint: stringValue(ocr.endpoint).trim(),
    model: stringValue(ocr.model).trim(),
  }
  const ocrKey = stringValue(ocr.api_key).trim()
  if (ocrKey) ocrPayload.api_key = ocrKey

  return {
    notification_config: notificationPayload,
    ocr_config: ocrPayload,
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
  const [notification, setNotification] = useState({ enabled: false, provider: '', url: '', token: '', tg_chat_id: '' })
  const [ocr, setOcr] = useState({ enabled: false, provider: '', endpoint: '', model: '', api_key: '' })
  const originalNotificationRef = useRef({})
  const originalOcrRef = useRef({})
  const requestId = useRef(0)
  const testGeneration = useRef(0)
  const connectionRef = useRef(connection)
  const apiKeyRef = useRef(apiKey)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const [runtimeError, setRuntimeError] = useState('')
  const [accountError, setAccountError] = useState('')
  const [success, setSuccess] = useState('')
  const [testState, setTestState] = useState('idle')
  const [testMessage, setTestMessage] = useState('')
  const [connectionSuccess, setConnectionSuccess] = useState('')
  const [savingConnection, setSavingConnection] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearingKey, setClearingKey] = useState(false)
  const [accountPrefsReady, setAccountPrefsReady] = useState(!initialAccountId)

  useEffect(() => {
    connectionRef.current = connection
  }, [connection])

  useEffect(() => {
    apiKeyRef.current = apiKey
  }, [apiKey])

  useEffect(() => {
    setSelectedAccountId((current) => {
      if (current && (current === explicitAccountId || accounts.some((account) => String(account.id) === String(current)))) return current
      return explicitAccountId ?? accountFromQuery ?? accounts[0]?.id ?? ''
    })
  }, [accountFromQuery, accounts, explicitAccountId])

  const loadSettings = useCallback(async () => {
    const currentRequest = ++requestId.current
    testGeneration.current += 1
    setLoading(true)
    setLoadError('')
    setConnectionError('')
    setRuntimeError('')
    setAccountError('')
    setSuccess('')
    setConnectionSuccess('')
    setTestState('idle')
    setTestMessage('')
    setAccountPrefsReady(!selectedAccountId)

    const requests = [getAnswerConnection(), getRuntimeSettings()]
    if (selectedAccountId) requests.push(getPreferences(selectedAccountId))
    const [connectionResult, runtimeResult, accountResult] = await Promise.allSettled(requests)
    if (currentRequest !== requestId.current) return

    const failures = []
    if (connectionResult.status === 'fulfilled') {
      const nextConnection = normalizeConnection(connectionResult.value)
      connectionRef.current = nextConnection
      setConnection(nextConnection)
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
        if (
          !source
          || typeof source !== 'object'
          || Array.isArray(source)
          || !source.notification_config
          || typeof source.notification_config !== 'object'
          || Array.isArray(source.notification_config)
          || !source.ocr_config
          || typeof source.ocr_config !== 'object'
          || Array.isArray(source.ocr_config)
        ) {
          setAccountPrefsReady(false)
          setAccountError('账户通知与 OCR 设置加载失败')
          setSuccess('')
          setLoadError(failures.join('；'))
          setLoading(false)
          return
        }
        const notificationSource = safeConfig(source.notification_config, { notification: true })
        const ocrSource = safeConfig(source.ocr_config)
        originalNotificationRef.current = notificationSource
        originalOcrRef.current = ocrSource
        setNotification({
          enabled: notificationSource.enabled === true,
          provider: stringValue(notificationSource.provider),
          // Notification destinations are replacement-only inputs.  The
          // response carries only has_*/mask metadata, never the saved URL.
          url: '',
          token: '',
          tg_chat_id: '',
        })
        setOcr({
          enabled: ocrSource.enabled === true,
          provider: stringValue(ocrSource.provider),
          // ``endpoint`` is the runtime's canonical OCR field.  Accept the
          // legacy base_url shape only as a read compatibility bridge.
          endpoint: stringValue(ocrSource.endpoint ?? ocrSource.base_url),
          model: stringValue(ocrSource.model),
          api_key: '',
        })
        setAccountPrefsReady(true)
      } else {
        setAccountPrefsReady(false)
        setAccountError(errorMessage(accountResult.reason, '账户通知与 OCR 设置加载失败'))
        setSuccess('')
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

  const invalidateConnectionTest = () => {
    testGeneration.current += 1
    setTestState('idle')
    setTestMessage('')
  }

  const updateConnection = (field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setConnection((current) => {
      const next = { ...current, [field]: value }
      connectionRef.current = next
      return next
    })
    setConnectionError('')
    setRuntimeError('')
    setSuccess('')
    setConnectionSuccess('')
    invalidateConnectionTest()
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
    const generation = ++testGeneration.current
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    setTestState('testing')
    setTestMessage('')
    setConnectionError('')
    try {
      const result = unwrap(await testAnswerConnection(answerPayload(connection, apiKey)), ['result']) || {}
      if (
        generation !== testGeneration.current
        || fingerprint !== connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)
      ) return
      if (result.ok === true && result.model_found !== false) {
        setTestState('success')
        setTestMessage('连接成功，模型可用')
        return
      }
      setTestState('error')
      setTestMessage(errorMessage(result, '连接失败'))
    } catch (error) {
      if (
        generation !== testGeneration.current
        || fingerprint !== connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)
      ) return
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
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    invalidateConnectionTest()
    setConnectionSuccess('')
    setSavingConnection(true)
    setConnectionError('')
    setSuccess('')
    try {
      const saved = await saveAnswerConnection(answerPayload(connection, apiKey))
      if (fingerprint === connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)) {
        if (saved) {
          const nextConnection = { ...connectionRef.current, ...normalizeConnection(saved) }
          connectionRef.current = nextConnection
          setConnection(nextConnection)
        }
        apiKeyRef.current = ''
        setApiKey('')
      }
      setConnectionSuccess('连接设置已保存')
    } catch (error) {
      setConnectionError(errorMessage(error, '连接设置保存失败，请重试'))
    } finally {
      testGeneration.current += 1
      setTestState('idle')
      setTestMessage('')
      setSavingConnection(false)
    }
  }

  const handleClearKey = async () => {
    if (!confirmingClear || clearingKey) return
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    invalidateConnectionTest()
    setConnectionSuccess('')
    setClearingKey(true)
    setConnectionError('')
    try {
      const cleared = await clearAnswerKey()
      if (fingerprint === connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)) {
        const nextConnection = {
          ...connectionRef.current,
          ...(cleared ? normalizeConnection(cleared) : {}),
          has_api_key: false,
          api_key_mask: null,
        }
        connectionRef.current = nextConnection
        setConnection(nextConnection)
        apiKeyRef.current = ''
        setApiKey('')
      } else {
        // Keep edits made while the request was in flight, while reflecting
        // the server-side key removal in the non-draft status fields.
        setConnection((current) => ({ ...current, has_api_key: false, api_key_mask: null }))
      }
      setConfirmingClear(false)
      setConnectionSuccess('API Key 已清除')
    } catch (error) {
      setConnectionError(errorMessage(error, 'API Key 清除失败，请重试'))
    } finally {
      testGeneration.current += 1
      setTestState('idle')
      setTestMessage('')
      setClearingKey(false)
    }
  }

  const handleSaveSettings = async () => {
    setAccountError('')
    setConnectionSuccess('')
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
    if (selectedAccountId && !accountPrefsReady) {
      setSuccess('')
      setAccountError('账户通知与 OCR 设置尚未成功加载，未保存任何设置。请重试加载。')
      return
    }

    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    invalidateConnectionTest()
    setSavingSettings(true)
    setRuntimeError('')
    setConnectionError('')
    setSuccess('')
    let accountSaveStarted = false
    try {
      await saveRuntimeSettings({ max_active_accounts: Number(runtime.max_active_accounts) })
      const savedConnection = await saveAnswerConnection(answerPayload(connection, apiKey))
      if (savedConnection && fingerprint === connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)) {
        const nextConnection = { ...connectionRef.current, ...normalizeConnection(savedConnection) }
        connectionRef.current = nextConnection
        setConnection(nextConnection)
      }
      if (selectedAccountId && accountPrefsReady) {
        accountSaveStarted = true
        const accountPayload = preferenceConfigPayload(
          notification,
          ocr,
          originalNotificationRef.current,
          originalOcrRef.current,
        )
        await savePreferences(selectedAccountId, accountPayload)
        originalNotificationRef.current = safeConfig(accountPayload.notification_config, { notification: true })
        originalOcrRef.current = safeConfig(accountPayload.ocr_config)
        setNotification((current) => ({ ...current, url: '', token: '', tg_chat_id: '' }))
        setOcr((current) => ({ ...current, api_key: '' }))
        setAccountError('')
      }
      if (fingerprint === connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)) {
        apiKeyRef.current = ''
        setApiKey('')
      }
      setSuccess('设置已保存')
    } catch (error) {
      if (accountSaveStarted) {
        setAccountError(errorMessage(error, '账户通知与 OCR 设置保存失败，请重试'))
        setSuccess('')
      } else {
        setRuntimeError(errorMessage(error, '设置保存失败，请重试'))
      }
    } finally {
      testGeneration.current += 1
      setTestState('idle')
      setTestMessage('')
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
            {connectionSuccess ? <Alert variant="success" aria-live="polite">{connectionSuccess}</Alert> : null}
            {connectionError ? <Alert variant="danger" aria-live="polite">{connectionError}</Alert> : null}
            {testMessage ? (
              <Alert variant={testState === 'success' ? 'success' : 'danger'} aria-live="polite">{testMessage}</Alert>
            ) : null}
            <p className="text-xs text-label-secondary" role="status" aria-live="polite" data-testid="connection-test-status">
              {testState === 'testing'
                ? '正在测试当前连接…'
                : testState === 'success'
                  ? '当前连接已通过测试。'
                  : '尚未测试当前连接，请重新测试。'}
            </p>

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
                  apiKeyRef.current = event.target.value
                  setApiKey(event.target.value)
                  setConnectionError('')
                  setSuccess('')
                  setConnectionSuccess('')
                  invalidateConnectionTest()
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

        <details open={Boolean(accountError)} className="group border-b border-separator">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>通知</span>
            <span aria-hidden="true" className="text-label-tertiary transition-transform group-open:rotate-180 motion-reduce:transition-none">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            {accountError ? (
              <Alert variant="danger" aria-live="polite">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span>{accountError}</span>
                  <Button type="button" variant="outline" onClick={loadSettings}>重新加载账户设置</Button>
                </div>
              </Alert>
            ) : null}
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
                <input type="checkbox" className="size-4 accent-accent-blue" checked={Boolean(notification.enabled)} onChange={updateAccountConfig(setNotification, 'enabled')} disabled={!selectedAccountId || !accountPrefsReady} />
              <span className="font-medium text-label-primary">启用通知</span>
            </label>
            <Field label="通知渠道" htmlFor="notification-provider">
              <Input id="notification-provider" value={notification.provider} onChange={updateAccountConfig(setNotification, 'provider')} disabled={!selectedAccountId || !accountPrefsReady} placeholder="例如：webhook" />
            </Field>
            <Field label="通知地址" htmlFor="notification-url">
              <Input id="notification-url" value={notification.url} onChange={updateAccountConfig(setNotification, 'url')} disabled={!selectedAccountId || !accountPrefsReady} placeholder="https://…" />
            </Field>
            <Field label="通知 Token" htmlFor="notification-token" description={configuredSecret(originalNotificationRef.current, ['token']) ? '已有 Token；留空表示保留' : '可选'}>
              <Input id="notification-token" type="password" value={notification.token} onChange={updateAccountConfig(setNotification, 'token')} disabled={!selectedAccountId || !accountPrefsReady} autoComplete="new-password" />
            </Field>
            <Field label="替换 Telegram Chat ID" htmlFor="notification-chat-id" description={configuredSecret(originalNotificationRef.current, ['chat_id', 'tg_chat_id']) ? '已有 Chat ID；留空表示保留' : '可选'}>
              <Input id="notification-chat-id" type="password" value={notification.tg_chat_id} onChange={updateAccountConfig(setNotification, 'tg_chat_id')} disabled={!selectedAccountId || !accountPrefsReady} autoComplete="new-password" />
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
              <input type="checkbox" className="size-4 accent-accent-blue" checked={Boolean(ocr.enabled)} onChange={updateAccountConfig(setOcr, 'enabled')} disabled={!selectedAccountId || !accountPrefsReady} />
              <span className="font-medium text-label-primary">启用 OCR</span>
            </label>
            <Field label="OCR 提供方" htmlFor="ocr-provider">
              <Input id="ocr-provider" value={ocr.provider} onChange={updateAccountConfig(setOcr, 'provider')} disabled={!selectedAccountId || !accountPrefsReady} placeholder="例如：openai" />
            </Field>
            <Field label="OCR 地址" htmlFor="ocr-endpoint">
              <Input id="ocr-endpoint" value={ocr.endpoint} onChange={updateAccountConfig(setOcr, 'endpoint')} disabled={!selectedAccountId || !accountPrefsReady} placeholder="https://…" />
            </Field>
            <Field label="OCR 模型" htmlFor="ocr-model">
              <Input id="ocr-model" value={ocr.model} onChange={updateAccountConfig(setOcr, 'model')} disabled={!selectedAccountId || !accountPrefsReady} />
            </Field>
            <Field label="替换 OCR API Key" htmlFor="ocr-api-key" description={configuredSecret(originalOcrRef.current, ['api']) ? '已有 OCR Key；留空表示保留' : '可选'}>
              <Input id="ocr-api-key" type="password" value={ocr.api_key} onChange={updateAccountConfig(setOcr, 'api_key')} disabled={!selectedAccountId || !accountPrefsReady} autoComplete="new-password" />
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
