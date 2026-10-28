'use client'

import { useEffect, useRef, type ReactNode, type RefObject } from 'react'

interface Props {
  labelledBy: string
  onClose: () => void
  children: ReactNode
  className?: string
  initialFocusRef?: RefObject<HTMLElement>
}

export default function Dialog({
  labelledBy,
  onClose,
  children,
  className = '',
  initialFocusRef,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    const previousFocus = document.activeElement
    dialog.showModal()
    initialFocusRef?.current?.focus({ preventScroll: true })

    return () => {
      dialog.close()
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true })
      }
    }
  }, [initialFocusRef])

  return (
    <dialog
      ref={ref}
      aria-labelledby={labelledBy}
      className={`ui-dialog ${className}`}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], summary, [tabindex="0"]'
          )
        ).filter(
          (element) =>
            element.tabIndex >= 0 &&
            element.getClientRects().length > 0 &&
            getComputedStyle(element).visibility !== 'hidden'
        )
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first?.focus()
        }
      }}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return
        const rect = event.currentTarget.getBoundingClientRect()
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        ) {
          onClose()
        }
      }}
    >
      {children}
    </dialog>
  )
}
