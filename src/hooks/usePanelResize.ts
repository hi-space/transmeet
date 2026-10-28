'use client'

import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type PointerEvent,
  type KeyboardEvent,
} from 'react'

interface Options {
  storageKey: string
  defaultWidth: number
  minWidth: number
  maxWidth: number
  /** The panel edge on which the resize handle is placed. */
  edge: 'left' | 'right'
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

export function usePanelResize({ storageKey, defaultWidth, minWidth, maxWidth, edge }: Options) {
  const [width, setWidth] = useState(() => clamp(defaultWidth, minWidth, maxWidth))
  const widthRef = useRef(width)
  const dragCleanup = useRef<(() => void) | null>(null)
  const direction = edge === 'right' ? 1 : -1

  const updateWidth = useCallback(
    (value: number) => {
      const next = clamp(value, minWidth, maxWidth)
      widthRef.current = next
      setWidth(next)
    },
    [minWidth, maxWidth]
  )

  const persistWidth = useCallback(
    (value: number) => {
      try {
        localStorage.setItem(storageKey, String(clamp(value, minWidth, maxWidth)))
      } catch {
        // Resizing still works when browser storage is unavailable.
      }
    },
    [storageKey, minWidth, maxWidth]
  )

  useEffect(() => {
    try {
      const stored = Number(localStorage.getItem(storageKey))
      updateWidth(
        Number.isFinite(stored) && stored >= minWidth && stored <= maxWidth ? stored : defaultWidth
      )
    } catch {
      updateWidth(defaultWidth)
    }
    return () => dragCleanup.current?.()
  }, [storageKey, defaultWidth, minWidth, maxWidth, updateWidth])

  const handlePointerDown = useCallback(
    (event: PointerEvent) => {
      if (event.button !== 0) return
      event.preventDefault()
      dragCleanup.current?.()
      const { clientX: startX, pointerId } = event
      const startWidth = widthRef.current
      const cursor = document.body.style.cursor
      const userSelect = document.body.style.userSelect

      const move = (next: globalThis.PointerEvent) => {
        if (next.pointerId !== pointerId) return
        updateWidth(startWidth + (next.clientX - startX) * direction)
      }
      const finish = (end?: globalThis.PointerEvent) => {
        if (end && end.pointerId !== pointerId) return
        document.removeEventListener('pointermove', move)
        document.removeEventListener('pointerup', finish)
        document.removeEventListener('pointercancel', finish)
        document.body.style.cursor = cursor
        document.body.style.userSelect = userSelect
        persistWidth(widthRef.current)
        dragCleanup.current = null
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      document.addEventListener('pointermove', move)
      document.addEventListener('pointerup', finish)
      document.addEventListener('pointercancel', finish)
      dragCleanup.current = finish
    },
    [direction, persistWidth, updateWidth]
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      let next = widthRef.current
      if (event.key === 'ArrowRight') next += 20 * direction
      else if (event.key === 'ArrowLeft') next -= 20 * direction
      else if (event.key === 'Home') next = minWidth
      else if (event.key === 'End') next = maxWidth
      else return
      event.preventDefault()
      updateWidth(next)
      persistWidth(next)
    },
    [direction, minWidth, maxWidth, persistWidth, updateWidth]
  )

  return { width, minWidth, maxWidth, handlePointerDown, handleKeyDown, persistWidth }
}
