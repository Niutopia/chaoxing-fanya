import React, { useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { createAccount, updateAccount, verifyAccount } from '../../api/accounts'
import Alert from '../ui/Alert'
import Button from '../ui/Button'
import Field from '../ui/Field'
import Input from '../ui/Input'

const EMPTY_FORM = {
  name: '',
  username: '',
  authMode: 'password',
  password: '',
  cookies: '',
}

function accountValue(account, snakeCase, camelCase = snakeCase) {
  if (!account) return undefined
  return account[snakeCase] ?? account[camelCase]
}

function formForAccount(account) {
  if (!account) return { ...EMPTY_FORM }

  const hasSecret = Boolean(accountValue(account, 'has_secret', 'hasSecret'))
  const hasCookies = Boolean(accountValue(account, 'has_cookies', 'hasCookies'))

  return {
    ...EMPTY_FORM,
    name: String(account.name ?? ''),
    username: String(account.username ?? ''),
    authMode: hasCookies && !hasSecret ? 'cookies' : 'password',
  }
}

function errorMessage(error, fallback) {
  const message = error?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

const PUBLIC_ACCOUNT_FIELDS = [
  'id',
  'name',
  'username',
  'enabled',
  'has_secret',
  'has_cookies',
  'verification_status',
  'last_verified_at',
]

const PUBLIC_ACCOUNT_ALIASES = {
  has_secret: 'hasSecret',
  has_cookies: 'hasCookies',
  verification_status: 'verificationStatus',
  last_verified_at: 'lastVerifiedAt',
}

function publicAccount(value) {
  if (!value || typeof value !== 'object') return null
  return PUBLIC_ACCOUNT_FIELDS.reduce((result, field) => {
    const sourceField = value[field] !== undefined ? field : PUBLIC_ACCOUNT_ALIASES[field]
    if (sourceField && value[sourceField] !== undefined) result[field] = value[sourceField]
    return result
  }, {})
}

/**
 * Account creation and editing form.
 *
 * Account API responses intentionally expose only ``has_secret`` and
 * ``has_cookies`` metadata.  The form therefore initializes secret controls
 * to empty strings and only sends a replacement when the user enters one.
 */
function AccountDialog({ open = false, account = null, onOpenChange, onSaved }) {
  const [form, setForm] = useState(() => formForAccount(account))
  const [saving, setSaving] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const nameRef = useRef(null)

  useEffect(() => {
    if (open) {
      setForm(formForAccount(account))
      setError('')
      setSuccess('')
    }
  }, [account, open])

  const isEditing = Boolean(account?.id)
  const title = isEditing ? '编辑账户' : '添加账户'

  const setField = (field) => (event) => {
    const value = event.target.value
    setForm((current) => ({ ...current, [field]: value }))
    if (error) setError('')
    if (success) setSuccess('')
  }

  const validate = ({ requireSecret = false } = {}) => {
    if (!form.name.trim()) return '请输入账户名称'
    if (!form.username.trim()) return '请输入手机号'
    if (requireSecret && !form.password.trim() && !form.cookies.trim()) {
      return '请输入密码或 Cookie Header'
    }
    return ''
  }

  const payload = () => {
    const values = {
      name: form.name.trim(),
      username: form.username.trim(),
    }
    // An empty replacement must be omitted.  The account API treats an
    // omitted password as "keep the stored encrypted secret".
    if (form.password.trim()) values.password = form.password
    if (form.cookies.trim()) values.cookies = form.cookies.trim()
    return values
  }

  const savedAccount = async () => {
    const values = payload()
    if (isEditing) return updateAccount(account.id, values)
    return createAccount(values)
  }

  const notifySaved = (saved) => {
    const safeSaved = publicAccount(saved)
    if (typeof onSaved === 'function' && safeSaved?.id) onSaved(safeSaved)
    return safeSaved
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (saving || verifying) return

    const validation = validate({ requireSecret: !isEditing })
    if (validation) {
      setError(validation)
      setSuccess('')
      return
    }

    setSaving(true)
    setError('')
    setSuccess('')
    try {
      const saved = await savedAccount()
      const safeSaved = notifySaved(saved)
      if (!isEditing) {
        const id = safeSaved?.id
        if (!id) throw new Error('账户保存成功，但缺少账户标识')
        const verified = await verifyAccount(id)
        notifySaved(verified || safeSaved)
        setSuccess('账户验证成功')
      } else {
        setSuccess('账户已保存')
      }
      // Do not retain secrets after a successful request.  The stored API
      // response is metadata-only and is never copied into these controls.
      setForm((current) => ({ ...current, password: '', cookies: '' }))
    } catch (requestError) {
      setError(errorMessage(requestError, '保存账户失败，请重试'))
    } finally {
      setSaving(false)
    }
  }

  const handleVerify = async () => {
    if (!isEditing || saving || verifying) return
    const validation = validate()
    if (validation) {
      setError(validation)
      setSuccess('')
      return
    }

    setVerifying(true)
    setError('')
    setSuccess('')
    try {
      const verified = await verifyAccount(account.id)
      notifySaved(verified || account)
      setSuccess('账户验证成功')
      setForm((current) => ({ ...current, password: '', cookies: '' }))
    } catch (requestError) {
      setError(errorMessage(requestError, '账户验证失败，请重试'))
    } finally {
      setVerifying(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[min(720px,calc(100dvh-32px))] w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-separator bg-surface p-5 text-label-primary outline-none"
          aria-describedby="account-dialog-description"
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            nameRef.current?.focus()
          }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold tracking-tight">
                {title}
              </Dialog.Title>
              <Dialog.Description
                id="account-dialog-description"
                className="mt-1 text-sm leading-5 text-label-secondary"
              >
                使用独立的账户凭据启动学习任务。保存的密码和 Cookie 不会显示在这里。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="touch-target touch-target-compact inline-flex size-8 shrink-0 items-center justify-center rounded-md text-label-secondary hover:bg-black/[0.06] hover:text-label-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                aria-label="关闭账户对话框"
              >
                <X aria-hidden="true" size={17} strokeWidth={1.8} />
              </button>
            </Dialog.Close>
          </div>

          <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
            {error ? (
              <Alert variant="danger" aria-live="polite">
                {error}
              </Alert>
            ) : null}
            {success ? (
              <Alert variant="success" aria-live="polite">
                {success}
              </Alert>
            ) : null}

            <Field label="账户名称" htmlFor="account-name">
              <Input
                ref={nameRef}
                id="account-name"
                value={form.name}
                onChange={setField('name')}
                autoComplete="organization"
                placeholder="例如：张三"
              />
            </Field>

            <Field label="手机号" htmlFor="account-username">
              <Input
                id="account-username"
                value={form.username}
                onChange={setField('username')}
                inputMode="tel"
                autoComplete="username"
                placeholder="用于登录超星学习通"
              />
            </Field>

            <Field
              label="认证方式"
              htmlFor="account-auth-mode"
              description="可在密码登录和 Cookie Header 之间切换。"
            >
              <select
                id="account-auth-mode"
                value={form.authMode}
                onChange={setField('authMode')}
                className="touch-target touch-target-compact flex h-9 w-full rounded-md border border-separator bg-surface px-2.5 py-1.5 text-sm text-label-primary outline-none focus-visible:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue/20"
              >
                <option value="password">密码登录</option>
                <option value="cookies">Cookie Header</option>
              </select>
            </Field>

            <Field
              label="密码"
              htmlFor="account-password"
              description={
                isEditing && accountValue(account, 'has_secret', 'hasSecret')
                  ? '已保存密码；留空表示不修改'
                  : '仅在需要替换密码时填写。'
              }
            >
              <Input
                id="account-password"
                type="password"
                value={form.password}
                onChange={setField('password')}
                autoComplete={isEditing ? 'new-password' : 'current-password'}
                placeholder={isEditing ? '留空表示不修改' : '输入密码'}
              />
            </Field>

            <Field
              label="Cookie Header"
              htmlFor="account-cookies"
              description={
                isEditing && accountValue(account, 'has_cookies', 'hasCookies')
                  ? '已保存 Cookie；留空表示不修改'
                  : '可选，格式为 name=value; name2=value2。'
              }
            >
              <textarea
                id="account-cookies"
                value={form.cookies}
                onChange={setField('cookies')}
                rows={3}
                autoComplete="off"
                spellCheck="false"
                placeholder="留空或粘贴 Cookie Header"
                className="touch-target flex min-h-20 w-full resize-y rounded-md border border-separator bg-surface px-2.5 py-2 text-sm leading-5 text-label-primary outline-none placeholder:text-label-tertiary focus-visible:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue/20"
              />
            </Field>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-separator pt-4">
              {isEditing ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleVerify}
                  loading={verifying}
                  disabled={saving}
                >
                  验证账户
                </Button>
              ) : null}
              <Dialog.Close asChild>
                <Button type="button" variant="ghost">
                  取消
                </Button>
              </Dialog.Close>
              <Button type="submit" loading={saving}>
                {isEditing ? '保存账户' : '验证并保存'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export { AccountDialog, publicAccount }
export default AccountDialog
