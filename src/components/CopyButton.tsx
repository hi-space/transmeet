'use client'

import { useEffect, useRef, useState } from 'react'
import Icon from '@/components/ui/Icon'
import type { ClipboardFormat } from '@/lib/clipboard'

interface Props {
  label: string
  title: string
  disabled?: boolean
  onCopy: () => Promise<ClipboardFormat | void>
  compact?: boolean
  compactLabel?: string
}

export default function CopyButton({
  label,
  title,
  disabled,
  onCopy,
  compact = false,
  compactLabel,
}: Props) {
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'text' | 'error'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const copying = useRef(false)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      clearTimeout(timer.current)
    }
  }, [])

  const handleCopy = async () => {
    if (copying.current) return
    copying.current = true
    clearTimeout(timer.current)
    setState('busy')
    try {
      const format = await onCopy()
      if (mounted.current) setState(format === 'text' ? 'text' : 'done')
    } catch {
      if (mounted.current) setState('error')
    } finally {
      copying.current = false
      if (mounted.current) timer.current = setTimeout(() => setState('idle'), 2500)
    }
  }

  const feedback = {
    idle: '',
    busy: '복사 중…',
    done: '복사됨',
    text: '텍스트 복사됨',
    error: '복사 실패',
  }[state]
  const description =
    state === 'text'
      ? '이 브라우저에서는 서식 없이 텍스트로 복사했습니다.'
      : state === 'error'
        ? '복사하지 못했습니다. 브라우저의 클립보드 권한을 확인하고 다시 시도해 주세요.'
        : title
  const showCompactLabel = compact && compactLabel && state === 'idle'

  return (
    <>
      <button
        type="button"
        onClick={handleCopy}
        disabled={disabled || state === 'busy'}
        aria-busy={state === 'busy'}
        title={description}
        className={`ui-button gap-1.5 px-2 ${compact ? 'max-lg:h-11 max-lg:w-11 max-lg:gap-0 max-lg:p-0' : ''} ${state === 'error' ? 'text-red-600 dark:text-red-400' : ''}`}
      >
        <Icon
          name={
            state === 'busy'
              ? 'loader'
              : state === 'error'
                ? 'close'
                : state === 'done' || state === 'text'
                  ? 'check'
                  : 'copy'
          }
          className={`h-3.5 w-3.5 ${showCompactLabel ? 'hidden lg:block' : ''} ${state === 'busy' ? 'animate-spin' : ''}`}
        />
        {showCompactLabel && (
          <span aria-hidden="true" className="text-xs lg:hidden">
            {compactLabel}
          </span>
        )}
        <span className={compact ? 'sr-only lg:not-sr-only' : undefined}>{feedback || label}</span>
      </button>
      <span role="status" className="sr-only">
        {state === 'error' || state === 'text' ? description : feedback && `${label}: ${feedback}`}
      </span>
    </>
  )
}
