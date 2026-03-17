'use client'

import { useEffect, useRef } from 'react'

/**
 * Runs callback on a fixed interval.
 * Pass null as delayMs to pause the interval.
 * Always calls the latest version of callback (no stale closure).
 */
export function useInterval(callback: () => void, delayMs: number | null) {
  const savedCallback = useRef(callback)

  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  useEffect(() => {
    if (delayMs === null) return
    const id = setInterval(() => savedCallback.current(), delayMs)
    return () => clearInterval(id)
  }, [delayMs])
}
