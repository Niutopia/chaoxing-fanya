import { useEffect } from 'react'
import { toApiError } from '../api/client'

/**
 * Run an async loader immediately and then at a fixed interval while enabled.
 * The next timer is scheduled only after the current request settles, keeping
 * terminal/task polling from overlapping requests and making cleanup safe.
 */
export function usePolling(loader, { enabled, intervalMs = 2000, onData, onError } = {}) {
  useEffect(() => {
    if (!enabled) return undefined

    let active = true
    let timer
    let roundController = null

    const poll = async () => {
      // Every request gets its own controller.  Aborting the active round on
      // cleanup prevents a route change/unmount from leaving a network call
      // alive, while keeping the next round independent from this one.
      const controller = new AbortController()
      roundController = controller
      try {
        const data = await loader(controller.signal)
        if (active) onData?.(data)
      } catch (error) {
        const aborted = controller.signal.aborted
          || error?.name === 'AbortError'
          || error?.code === 'ERR_CANCELED'
        if (active && !aborted) onError?.(toApiError(error))
      } finally {
        if (roundController === controller) roundController = null
        if (active) timer = window.setTimeout(poll, intervalMs)
      }
    }

    poll()

    return () => {
      active = false
      window.clearTimeout(timer)
      roundController?.abort()
      roundController = null
    }
  }, [enabled, intervalMs, loader, onData, onError])
}

export default usePolling
