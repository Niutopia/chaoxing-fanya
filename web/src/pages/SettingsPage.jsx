import React, { useCallback, useEffect, useRef, useState } from 'react'
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

const DEFAULT_RUNTIME = { max_active_accounts: 3 }
const CONFIG_MASK = 'Configured (••••)'

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

function isAborted(error, signal) {
  return Boolean(signal?.aborted) || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED'
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
  return CONFIG_MASK
}

function normalizeConnection(value) {
  const source = unwrap(value, ['connection', 'answer_connection'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) return { ...DEFAULT_CONNECTION }
  return {
    enabled: source.enabled === true,
    base_url: stringValue(source.base_url, DEFAULT_CONNECTION.base_url),
    model: stringValue(source.model, DEFAULT_CONNECTION.model),
    has_api_key: source.has_api_key === true,
    api_key_mask: safeMask(source.api_key_mask, source.has_api_key === true),
    last_test_status: typeof source.last_test_status === 'string' ? source.last_test_status : 'untested',
    timeout_seconds: numberValue(source.timeout_seconds, DEFAULT_CONNECTION.timeout_seconds),
    max_retries: numberValue(source.max_retries, DEFAULT_CONNECTION.max_retries),
    max_concurrency: numberValue(source.max_concurrency, DEFAULT_CONNECTION.max_concurrency),
  }
}

function normalizeRuntime(value) {
  const source = unwrap(value, ['runtime', 'settings'])
  if (!source || typeof source !== 'object' || Array.isArray(source)) return { ...DEFAULT_RUNTIME }
  return {
    max_active_accounts: numberValue(
      source.max_active_accounts ?? source.max_concurrent_accounts,
      DEFAULT_RUNTIME.max_active_accounts,
    ),
  }
}

function connectionSource(value) {
  const source = unwrap(value, ['connection', 'answer_connection'])
  return source && typeof source === 'object' && !Array.isArray(source) ? source : null
}

function hasOwn(value, key) {
  return Object.prototype.hasOwnProperty.call(value, key)
}

function connectionResponseFields(value) {
  const source = connectionSource(value)
  if (!source) return {}
  const fields = {}
  if (hasOwn(source, 'enabled')) fields.enabled = source.enabled === true
  if (hasOwn(source, 'base_url')) fields.base_url = stringValue(source.base_url, DEFAULT_CONNECTION.base_url)
  if (hasOwn(source, 'model')) fields.model = stringValue(source.model, DEFAULT_CONNECTION.model)
  if (hasOwn(source, 'timeout_seconds')) fields.timeout_seconds = numberValue(source.timeout_seconds, DEFAULT_CONNECTION.timeout_seconds)
  if (hasOwn(source, 'max_retries')) fields.max_retries = numberValue(source.max_retries, DEFAULT_CONNECTION.max_retries)
  if (hasOwn(source, 'max_concurrency')) fields.max_concurrency = numberValue(source.max_concurrency, DEFAULT_CONNECTION.max_concurrency)
  return fields
}

function safeConnectionMetadata(value, replacementApiKey = '') {
  const source = connectionSource(value)
  const metadata = {}
  if (source) {
    if (hasOwn(source, 'has_api_key')) metadata.has_api_key = source.has_api_key === true
    if (hasOwn(source, 'api_key_mask')) {
      const configured = metadata.has_api_key ?? source.api_key_mask != null
      metadata.api_key_mask = safeMask(source.api_key_mask, configured)
    }
    if (metadata.has_api_key === true && !hasOwn(metadata, 'api_key_mask')) metadata.api_key_mask = CONFIG_MASK
    if (metadata.has_api_key === false && !hasOwn(metadata, 'api_key_mask')) metadata.api_key_mask = null
    if (typeof source.last_test_status === 'string') metadata.last_test_status = source.last_test_status
  }
  if (typeof replacementApiKey === 'string' && replacementApiKey.trim() && !hasOwn(metadata, 'has_api_key')) {
    metadata.has_api_key = true
    if (!hasOwn(metadata, 'api_key_mask')) metadata.api_key_mask = CONFIG_MASK
  }
  return metadata
}

function mergeSavedConnection(current, saved, replacementApiKey, draftChanged) {
  const next = {
    ...current,
    ...(draftChanged ? {} : connectionResponseFields(saved)),
    ...safeConnectionMetadata(saved, replacementApiKey),
  }
  if (draftChanged) next.last_test_status = 'untested'
  return next
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
  if (!Number.isInteger(Number(connection.max_concurrency)) || Number(connection.max_concurrency) <= 0) return '请输入大于 0 的整数'
  if (!Number.isFinite(Number(connection.timeout_seconds)) || Number(connection.timeout_seconds) <= 0) return '请输入大于 0 的秒数'
  if (!Number.isInteger(Number(connection.max_retries)) || Number(connection.max_retries) < 0) return '请输入 0 或更大的整数'
  return ''
}

function validateRuntime(runtime) {
  const maxAccounts = Number(runtime.max_active_accounts)
  if (!Number.isInteger(maxAccounts) || maxAccounts < 1 || maxAccounts > 10) return '请输入 1 到 10'
  return ''
}

function SettingsPage({ className }) {
  const [connection, setConnection] = useState({ ...DEFAULT_CONNECTION })
  const [apiKey, setApiKey] = useState('')
  const [runtime, setRuntime] = useState({ ...DEFAULT_RUNTIME })
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const [runtimeError, setRuntimeError] = useState('')
  const [success, setSuccess] = useState('')
  const [connectionSuccess, setConnectionSuccess] = useState('')
  const [testState, setTestState] = useState('idle')
  const [testMessage, setTestMessage] = useState('')
  const [savingConnection, setSavingConnection] = useState(false)
  const [savingRuntime, setSavingRuntime] = useState(false)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearingKey, setClearingKey] = useState(false)

  const mountedRef = useRef(false)
  const requestIdRef = useRef(0)
  const testGenerationRef = useRef(0)
  const connectionRef = useRef(connection)
  const apiKeyRef = useRef(apiKey)
  const connectionMutationPendingRef = useRef(false)
  const connectionMutationOperationRef = useRef(0)
  const runtimeSaveOperationRef = useRef(0)
  const loadControllerRef = useRef(null)
  const testControllerRef = useRef(null)
  const connectionMutationControllerRef = useRef(null)
  const runtimeSaveControllerRef = useRef(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestIdRef.current += 1
      testGenerationRef.current += 1
      connectionMutationOperationRef.current += 1
      runtimeSaveOperationRef.current += 1
      connectionMutationPendingRef.current = false
      loadControllerRef.current?.abort()
      testControllerRef.current?.abort()
      connectionMutationControllerRef.current?.abort()
      runtimeSaveControllerRef.current?.abort()
    }
  }, [])

  useEffect(() => { connectionRef.current = connection }, [connection])
  useEffect(() => { apiKeyRef.current = apiKey }, [apiKey])

  const loadSettings = useCallback(async () => {
    loadControllerRef.current?.abort()
    testControllerRef.current?.abort()
    connectionMutationControllerRef.current?.abort()
    runtimeSaveControllerRef.current?.abort()
    const controller = new AbortController()
    const requestId = ++requestIdRef.current
    loadControllerRef.current = controller
    testGenerationRef.current += 1
    connectionMutationOperationRef.current += 1
    runtimeSaveOperationRef.current += 1
    connectionMutationPendingRef.current = false
    connectionMutationControllerRef.current = null
    runtimeSaveControllerRef.current = null
    setLoading(true)
    setLoadError('')
    setConnectionError('')
    setRuntimeError('')
    setSuccess('')
    setConnectionSuccess('')
    setTestState('idle')
    setTestMessage('')
    setSavingConnection(false)
    setSavingRuntime(false)
    setClearingKey(false)

    const [connectionResult, runtimeResult] = await Promise.allSettled([
      getAnswerConnection({ signal: controller.signal }),
      getRuntimeSettings({ signal: controller.signal }),
    ])
    if (!mountedRef.current || controller.signal.aborted || requestId !== requestIdRef.current) return
    const failures = []
    if (connectionResult.status === 'fulfilled') {
      const nextConnection = normalizeConnection(connectionResult.value)
      connectionRef.current = nextConnection
      setConnection(nextConnection)
    } else if (!isAborted(connectionResult.reason, controller.signal)) {
      failures.push(errorMessage(connectionResult.reason, '答题连接设置加载失败'))
    }
    if (runtimeResult.status === 'fulfilled') setRuntime(normalizeRuntime(runtimeResult.value))
    else if (!isAborted(runtimeResult.reason, controller.signal)) {
      failures.push(errorMessage(runtimeResult.reason, '运行设置加载失败'))
    }
    setLoadError(failures.join('；'))
    setLoading(false)
    if (loadControllerRef.current === controller) loadControllerRef.current = null
  }, [])

  useEffect(() => {
    loadSettings()
    return () => {
      requestIdRef.current += 1
      loadControllerRef.current?.abort()
      loadControllerRef.current = null
    }
  }, [loadSettings])

  const invalidateConnectionTest = () => {
    testGenerationRef.current += 1
    testControllerRef.current?.abort()
    testControllerRef.current = null
    setTestState('idle')
    setTestMessage('')
  }

  const beginConnectionMutation = () => {
    connectionMutationControllerRef.current?.abort()
    const controller = new AbortController()
    const operation = ++connectionMutationOperationRef.current
    connectionMutationPendingRef.current = true
    connectionMutationControllerRef.current = controller
    return { controller, operation }
  }

  const isCurrentConnectionMutation = (context) => (
    Boolean(context)
    && mountedRef.current
    && context.operation === connectionMutationOperationRef.current
    && connectionMutationControllerRef.current === context.controller
    && !context.controller.signal.aborted
  )

  const endConnectionMutation = (context) => {
    if (!isCurrentConnectionMutation(context)) return
    connectionMutationPendingRef.current = false
    connectionMutationControllerRef.current = null
  }

  const updateConnection = (field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value
    setConnection((current) => {
      const next = { ...current, [field]: value }
      connectionRef.current = next
      return next
    })
    setConnectionError('')
    setSuccess('')
    setConnectionSuccess('')
    invalidateConnectionTest()
  }

  const updateRuntime = (field) => (event) => {
    setRuntime((current) => ({ ...current, [field]: event.target.value }))
    setRuntimeError('')
    setSuccess('')
  }

  const handleTestConnection = async () => {
    if (connectionMutationPendingRef.current || savingConnection || clearingKey) return
    const validationError = validateConnection(connection)
    if (validationError) {
      setConnectionError(validationError)
      setTestMessage('')
      return
    }
    const generation = ++testGenerationRef.current
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    testControllerRef.current?.abort()
    const controller = new AbortController()
    testControllerRef.current = controller
    const isCurrentTest = () => (
      mountedRef.current
      && generation === testGenerationRef.current
      && testControllerRef.current === controller
      && !controller.signal.aborted
      && fingerprint === connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)
    )
    setTestState('testing')
    setTestMessage('')
    setConnectionError('')
    try {
      const result = unwrap(await testAnswerConnection(answerPayload(connection, apiKey), { signal: controller.signal }), ['result']) || {}
      if (!isCurrentTest()) return
      if (result.ok === true && result.model_found !== false) {
        setTestState('success')
        setTestMessage('连接成功，模型可用')
        return
      }
      setTestState('error')
      setTestMessage(errorMessage(result, '连接失败'))
    } catch (error) {
      if (!isCurrentTest() || isAborted(error, controller.signal)) return
      setTestState('error')
      setTestMessage(errorMessage(error, '连接失败'))
    } finally {
      if (testControllerRef.current === controller) testControllerRef.current = null
    }
  }

  const handleSaveConnection = async () => {
    if (connectionMutationPendingRef.current) return
    const validationError = validateConnection(connection)
    if (validationError) {
      setConnectionError(validationError)
      setSuccess('')
      return
    }
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    const submittedApiKey = apiKey
    const mutation = beginConnectionMutation()
    invalidateConnectionTest()
    setConnectionSuccess('')
    setSavingConnection(true)
    setConnectionError('')
    setSuccess('')
    try {
      const saved = await saveAnswerConnection(answerPayload(connection, apiKey), { signal: mutation.controller.signal })
      if (!isCurrentConnectionMutation(mutation)) return
      invalidateConnectionTest()
      const draftChanged = fingerprint !== connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)
      const nextConnection = mergeSavedConnection(connectionRef.current, saved, submittedApiKey, draftChanged)
      connectionRef.current = nextConnection
      setConnection(nextConnection)
      if (submittedApiKey === apiKeyRef.current) {
        apiKeyRef.current = ''
        setApiKey('')
      }
      setConnectionSuccess('连接设置已保存')
    } catch (error) {
      if (!isCurrentConnectionMutation(mutation) || isAborted(error, mutation.controller.signal)) return
      setConnectionError(errorMessage(error, '连接设置保存失败，请重试'))
    } finally {
      if (isCurrentConnectionMutation(mutation)) {
        invalidateConnectionTest()
        setSavingConnection(false)
      }
      endConnectionMutation(mutation)
    }
  }

  const handleClearKey = async () => {
    if (!confirmingClear || clearingKey || connectionMutationPendingRef.current) return
    const fingerprint = connectionDraftFingerprint(connection, apiKey)
    const submittedApiKey = apiKey
    const mutation = beginConnectionMutation()
    invalidateConnectionTest()
    setSuccess('')
    setConnectionSuccess('')
    setClearingKey(true)
    setConnectionError('')
    try {
      const cleared = await clearAnswerKey({ signal: mutation.controller.signal })
      if (!isCurrentConnectionMutation(mutation)) return
      invalidateConnectionTest()
      const draftChanged = fingerprint !== connectionDraftFingerprint(connectionRef.current, apiKeyRef.current)
      const nextConnection = {
        ...connectionRef.current,
        ...(draftChanged ? {} : connectionResponseFields(cleared)),
        ...safeConnectionMetadata(cleared),
        has_api_key: false,
        api_key_mask: null,
        last_test_status: 'untested',
      }
      connectionRef.current = nextConnection
      setConnection(nextConnection)
      if (submittedApiKey === apiKeyRef.current) {
        apiKeyRef.current = ''
        setApiKey('')
      }
      setConfirmingClear(false)
      setConnectionSuccess('API Key 已清除')
    } catch (error) {
      if (!isCurrentConnectionMutation(mutation) || isAborted(error, mutation.controller.signal)) return
      setConnectionError(errorMessage(error, 'API Key 清除失败，请重试'))
    } finally {
      if (isCurrentConnectionMutation(mutation)) {
        invalidateConnectionTest()
        setClearingKey(false)
      }
      endConnectionMutation(mutation)
    }
  }

  const handleSaveRuntime = async () => {
    if (runtimeSaveControllerRef.current) return
    const validationError = validateRuntime(runtime)
    if (validationError) {
      setRuntimeError(validationError)
      setSuccess('')
      return
    }
    runtimeSaveControllerRef.current?.abort()
    const controller = new AbortController()
    const operation = ++runtimeSaveOperationRef.current
    runtimeSaveControllerRef.current = controller
    const isCurrentSave = () => (
      mountedRef.current
      && operation === runtimeSaveOperationRef.current
      && runtimeSaveControllerRef.current === controller
      && !controller.signal.aborted
    )
    setSavingRuntime(true)
    setRuntimeError('')
    setSuccess('')
    try {
      await saveRuntimeSettings(
        { max_active_accounts: Number(runtime.max_active_accounts) },
        { signal: controller.signal },
      )
      if (!isCurrentSave()) return
      setSuccess('运行限制已保存')
    } catch (error) {
      if (!isCurrentSave() || isAborted(error, controller.signal)) return
      setRuntimeError(errorMessage(error, '运行限制保存失败，请重试'))
    } finally {
      if (isCurrentSave()) {
        setSavingRuntime(false)
        runtimeSaveControllerRef.current = null
      }
    }
  }

  if (loading) {
    return (
      <section className={cn('mx-auto w-full max-w-5xl px-4 py-8 md:px-8', className)} aria-labelledby="settings-title">
        <h1 id="settings-title" className="text-balance text-xl font-semibold">全局设置</h1>
        <p className="mt-2 text-pretty text-sm text-label-secondary" role="status" aria-live="polite">正在加载全局设置…</p>
      </section>
    )
  }

  const connectionMask = connection.has_api_key ? connection.api_key_mask || CONFIG_MASK : '未配置'
  const connectionPending = savingConnection || clearingKey

  return (
    <section className={cn('mx-auto w-full max-w-5xl px-4 py-7 md:px-8 md:py-8', className)} aria-labelledby="settings-title">
      <div>
        <p className="text-xs font-medium text-label-secondary">应用设置</p>
        <h1 id="settings-title" className="mt-1 text-balance text-xl font-semibold">全局设置</h1>
        <p className="mt-1 max-w-2xl text-pretty text-sm leading-5 text-label-secondary">
          这里的答题服务和运行限制对所有账户生效。课程与学习参数仍在对应账户的课程工作台中设置。
        </p>
      </div>

      {loadError ? (
        <Alert className="mt-5" variant="warning" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{loadError}</span>
            <Button type="button" variant="outline" onClick={loadSettings}>重新加载</Button>
          </div>
        </Alert>
      ) : null}
      {success ? <Alert className="mt-5" variant="success" aria-live="polite">{success}</Alert> : null}

      <div className="mt-7 border-y border-separator bg-surface">
        <details open className="group border-b border-separator">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>答题连接</span>
            <span aria-hidden="true" className="text-label-tertiary group-open:rotate-180">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            <p className="text-pretty text-sm leading-5 text-label-secondary">所有账户共享这一答题服务。</p>
            {connectionSuccess ? <Alert variant="success" aria-live="polite">{connectionSuccess}</Alert> : null}
            {connectionError ? <Alert variant="danger" aria-live="polite">{connectionError}</Alert> : null}
            {testMessage ? <Alert variant={testState === 'success' ? 'success' : 'danger'} aria-live="polite">{testMessage}</Alert> : null}
            <p className="text-xs text-label-secondary" role="status" aria-live="polite" data-testid="connection-test-status">
              {testState === 'testing' ? '正在测试当前连接…' : testState === 'success' ? '当前连接已通过测试。' : '尚未测试当前连接，请重新测试。'}
            </p>

            <label className="touch-target flex min-h-11 cursor-pointer items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2"
                checked={Boolean(connection.enabled)}
                disabled={connectionPending}
                onChange={updateConnection('enabled')}
              />
              <span>
                <span className="block font-medium text-label-primary">启用答题连接</span>
                <span className="mt-0.5 block text-xs text-label-secondary">答题开启后，任务会使用这个共享服务。</span>
              </span>
            </label>

            <Field label="基础地址" htmlFor="answer-base-url" description="例如 http://localhost:8849/v1。">
              <Input id="answer-base-url" value={connection.base_url} onChange={updateConnection('base_url')} autoComplete="url" disabled={connectionPending} />
            </Field>
            <Field label="模型" htmlFor="answer-model">
              <Input id="answer-model" value={connection.model} onChange={updateConnection('model')} autoComplete="off" disabled={connectionPending} />
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
                disabled={connectionPending}
              />
            </Field>
            <p className="text-xs text-label-secondary" role="status" aria-live="polite">
              当前 Key：<span className="font-medium text-label-primary">{connectionMask}</span>
            </p>

            <div className="grid gap-5 md:grid-cols-3">
              <Field label="请求超时" htmlFor="answer-timeout" description="秒">
                <Input id="answer-timeout" type="number" min="1" step="1" value={connection.timeout_seconds} onChange={updateConnection('timeout_seconds')} disabled={connectionPending} />
              </Field>
              <Field label="重试次数" htmlFor="answer-retries" description="可为 0">
                <Input id="answer-retries" type="number" min="0" step="1" value={connection.max_retries} onChange={updateConnection('max_retries')} disabled={connectionPending} />
              </Field>
              <Field label="全局答题并发数" htmlFor="answer-concurrency" description="大于 0 的整数">
                <Input id="answer-concurrency" type="number" min="1" step="1" value={connection.max_concurrency} onChange={updateConnection('max_concurrency')} disabled={connectionPending} />
              </Field>
            </div>

            <div className="flex flex-wrap justify-end gap-2 border-t border-separator pt-4">
              {connection.has_api_key ? (
                confirmingClear ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm text-label-secondary">
                    <span>确认清除已保存的 Key？</span>
                    <Button type="button" variant="outline" disabled={connectionPending} onClick={() => setConfirmingClear(false)}>取消</Button>
                    <Button type="button" variant="destructive" disabled={connectionPending} loading={clearingKey} onClick={handleClearKey}>确认清除</Button>
                  </div>
                ) : (
                  <Button type="button" variant="outline" disabled={connectionPending} onClick={() => setConfirmingClear(true)}>清除 API Key</Button>
                )
              ) : null}
              <Button type="button" variant="outline" disabled={connectionPending} loading={testState === 'testing'} onClick={handleTestConnection}>测试连接</Button>
              <Button type="button" disabled={connectionPending} onClick={handleSaveConnection} loading={savingConnection}>保存连接</Button>
            </div>
          </div>
        </details>

        <details open className="group">
          <summary className="touch-target flex min-h-12 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-inset [&::-webkit-details-marker]:hidden">
            <span>运行限制</span>
            <span aria-hidden="true" className="text-label-tertiary group-open:rotate-180">⌄</span>
          </summary>
          <div className="space-y-5 border-t border-separator px-4 py-4 md:px-5">
            <p className="text-pretty text-sm leading-5 text-label-secondary">限制整套应用同时运行的账户数量。</p>
            {runtimeError ? <Alert variant="danger" aria-live="polite">{runtimeError}</Alert> : null}
            <Field label="最大同时运行账户数" htmlFor="max-active-accounts" description="范围 1 到 10。">
              <Input id="max-active-accounts" type="number" min="1" max="10" step="1" value={runtime.max_active_accounts} onChange={updateRuntime('max_active_accounts')} disabled={savingRuntime} />
            </Field>
            <p className="text-pretty text-sm leading-5 text-label-secondary">答题并发数和请求超时在上方的答题连接中统一配置。</p>
            <div className="flex justify-end border-t border-separator pt-4">
              <Button type="button" disabled={savingRuntime} loading={savingRuntime} onClick={handleSaveRuntime}>保存运行限制</Button>
            </div>
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
  validateConnection,
  validateRuntime,
}
export default SettingsPage
