'use client'

import { useState, useRef, useEffect } from 'react'
import Icon from '@/components/ui/Icon'

interface Props {
  title: string
  onSave: (newTitle: string) => void
  className?: string
  inputClassName?: string
}

export default function EditableTitle({ title, onSave, className = '', inputClassName }: Props) {
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(title)
  const inputRef = useRef<HTMLInputElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [isEditing])

  useEffect(() => {
    if (!isEditing) setEditValue(title)
  }, [title, isEditing])

  function finish(save: boolean) {
    const value = editValue.trim()
    if (save && value && value !== title) onSave(value)
    setIsEditing(false)
    setEditValue(title)
    requestAnimationFrame(() => buttonRef.current?.focus())
  }

  if (isEditing) {
    return (
      <form
        className="flex min-w-0 max-w-full items-center gap-1"
        onSubmit={(event) => {
          event.preventDefault()
          finish(true)
        }}
      >
        <input
          ref={inputRef}
          aria-label="회의 제목"
          value={editValue}
          onChange={(event) => setEditValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) {
              if (event.key === 'Enter') event.preventDefault()
              return
            }
            if (event.key === 'Escape') {
              event.preventDefault()
              finish(false)
            }
          }}
          className={inputClassName ?? 'ui-input min-w-0 py-1.5 text-sm font-semibold'}
        />
        <button
          type="submit"
          disabled={!editValue.trim()}
          aria-label="제목 저장"
          className="ui-icon-button text-accent-700 dark:text-accent-300"
        >
          <Icon name="check" />
        </button>
        <button
          type="button"
          onClick={() => finish(false)}
          aria-label="제목 편집 취소"
          className="ui-icon-button"
        >
          <Icon name="close" />
        </button>
      </form>
    )
  }

  return (
    <h1 className="min-w-0">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => {
          setEditValue(title)
          setIsEditing(true)
        }}
        className={`group flex max-w-full items-center gap-2 rounded-md text-left ${className}`}
        aria-label={`${title} 제목 편집`}
        title="회의 제목 편집"
      >
        <span className="truncate">{title}</span>
        <Icon
          name="notes"
          className="h-3.5 w-3.5 text-muted opacity-50 transition-opacity group-hover:opacity-100"
        />
      </button>
    </h1>
  )
}
