'use client'

import { useState, useCallback, useEffect, useRef } from 'react'

const STORAGE_KEY = 'transmeet-summary-width'
const MIN_WIDTH = 280
const MAX_WIDTH = 600

function loadWidth(defaultWidth: number): number {
  if (typeof window === 'undefined') return defaultWidth
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultWidth
    const v = parseInt(raw, 10)
    return v >= MIN_WIDTH && v <= MAX_WIDTH ? v : defaultWidth
  } catch {
    return defaultWidth
  }
}

export function useSummaryResize(defaultWidth = 384) {
  const [width, setWidth] = useState(defaultWidth)
  const isDragging = useRef(false)

  // SSR-safe: load from localStorage after mount
  useEffect(() => {
    setWidth(loadWidth(defaultWidth))
  }, [defaultWidth])

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      isDragging.current = true
      const startX = e.clientX
      const startWidth = width

      const onMouseMove = (ev: MouseEvent) => {
        if (!isDragging.current) return
        // 왼쪽으로 드래그하면 너비 증가 (리사이즈 핸들이 왼쪽 가장자리)
        const delta = startX - ev.clientX
        const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + delta))
        setWidth(next)
      }

      const onMouseUp = () => {
        isDragging.current = false
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        // persist final width
        try {
          const el = document.querySelector('[data-summary-width]')
          const finalWidth = el ? parseInt(el.getAttribute('data-summary-width') || '', 10) : 0
          if (finalWidth >= MIN_WIDTH && finalWidth <= MAX_WIDTH) {
            localStorage.setItem(STORAGE_KEY, String(finalWidth))
          }
        } catch {
          // ignore
        }
      }

      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    },
    [width]
  )

  // persist on width change (debounced via mouseup above for drag)
  const persistWidth = useCallback((w: number) => {
    try {
      localStorage.setItem(STORAGE_KEY, String(w))
    } catch {
      // ignore
    }
  }, [])

  return { width, handleMouseDown, persistWidth }
}
