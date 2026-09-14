import { renderHook } from '@testing-library/react'
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
