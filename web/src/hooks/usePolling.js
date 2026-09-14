import { useEffect } from 'react'

/**
 * Run an async loader immediately and then at a fixed interval while enabled.
 * The next timer is scheduled only after the current request settles, keeping
 * terminal/task polling from overlapping requests and making cleanup safe.
 */
export function usePolling(loader, { enabled, intervalMs = 2000, onData } = {}) {
  useEffect(() => {
    if (!enabled) return undefined

    let active = true
    let timer

    const poll = async () => {
      try {
        const data = await loader()
        if (active) onData?.(data)
      } finally {
        if (active) timer = window.setTimeout(poll, intervalMs)
      }
    }

    poll()

    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [enabled, intervalMs, loader, onData])
}

export default usePolling
