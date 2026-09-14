import { renderHook } from '@testing-library/react'
import { ApiError } from '../api/client'
import { usePolling } from './usePolling'

test('stops polling when enabled becomes false', async () => {
  vi.useFakeTimers()
  const loader = vi.fn().mockResolvedValue({ state: 'running' })
  const { rerender } = renderHook(({ enabled }) => usePolling(loader, { enabled, intervalMs: 2000 }), {
    initialProps: { enabled: true },
  })
  await vi.advanceTimersByTimeAsync(4000)
  rerender({ enabled: false })
  await vi.advanceTimersByTimeAsync(4000)
  expect(loader).toHaveBeenCalledTimes(3)
  vi.useRealTimers()
})

test('reports loader rejection without creating an unhandled rejection', async () => {
  vi.useFakeTimers()
  const rejection = new Error('offline')
  const loader = vi.fn().mockRejectedValue(rejection)
  const onError = vi.fn()
  const unhandled = vi.fn()
  const previousHandler = globalThis.onunhandledrejection
  globalThis.onunhandledrejection = unhandled

  renderHook(() => usePolling(loader, { enabled: true, intervalMs: 2000, onError }))
  await Promise.resolve()

  expect(onError).toHaveBeenCalledTimes(1)
  expect(onError).toHaveBeenCalledWith(expect.any(ApiError))
  expect(onError.mock.calls[0][0]).toMatchObject({ message: 'offline' })
  expect(unhandled).not.toHaveBeenCalled()
  globalThis.onunhandledrejection = previousHandler
  vi.useRealTimers()
})

test('does not overlap requests and stops an in-flight cycle after disable', async () => {
  vi.useFakeTimers()
  let resolveLoader
  let activeRequests = 0
  let maxActiveRequests = 0
  const loader = vi.fn(() => {
    activeRequests += 1
    maxActiveRequests = Math.max(maxActiveRequests, activeRequests)
    return new Promise((resolve) => {
      resolveLoader = () => {
        activeRequests -= 1
        resolve({ state: 'running' })
      }
    })
  })
  const onData = vi.fn()
  const onError = vi.fn()
  const { rerender } = renderHook(
    ({ enabled }) => usePolling(loader, { enabled, intervalMs: 2000, onData, onError }),
    { initialProps: { enabled: true } },
  )

  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(4000)
  expect(loader).toHaveBeenCalledTimes(1)
  resolveLoader()
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(2000)
  expect(loader).toHaveBeenCalledTimes(2)
  expect(maxActiveRequests).toBe(1)

  rerender({ enabled: false })
  resolveLoader()
  await vi.advanceTimersByTimeAsync(4000)
  expect(loader).toHaveBeenCalledTimes(2)
  expect(onError).not.toHaveBeenCalled()
  vi.useRealTimers()
})

test('does not report an in-flight rejection after unmount', async () => {
  vi.useFakeTimers()
  let rejectLoader
  const loader = vi.fn(
    () =>
      new Promise((_, reject) => {
        rejectLoader = reject
      }),
  )
  const onError = vi.fn()
  const { unmount } = renderHook(() => usePolling(loader, { enabled: true, intervalMs: 2000, onError }))

  await Promise.resolve()
  unmount()
  rejectLoader(new Error('late offline'))
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(4000)

  expect(onError).not.toHaveBeenCalled()
  expect(loader).toHaveBeenCalledTimes(1)
  vi.useRealTimers()
})
