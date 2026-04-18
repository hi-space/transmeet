'use client'

import { useState, useRef, useEffect } from 'react'

interface Props {
  title: string
  onSave: (newTitle: string) => void
  className?: string
  inputClassName?: string
}

export default function EditableTitle({ title, onSave, className, inputClassName }: Props) {
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(title)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [isEditing])

  useEffect(() => {
    setEditValue(title)
  }, [title])

  function commit() {
    const trimmed = editValue.trim()
    setIsEditing(false)
    if (trimmed && trimmed !== title) {
      onSave(trimmed)
    } else {
      setEditValue(title)
    }
  }

  if (isEditing) {
    return (
      <input
        ref={inputRef}
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          } else if (e.key === 'Escape') {
            setIsEditing(false)
            setEditValue(title)
          }
        }}
        onBlur={commit}
        onClick={(e) => e.stopPropagation()}
        className={
          inputClassName ??
          'w-full text-sm font-medium bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-cyan-500 text-slate-700 dark:text-slate-200'
        }
      />
    )
  }

  return (
    <span
      onDoubleClick={() => {
        setIsEditing(true)
        setEditValue(title)
      }}
      className={className ?? 'text-sm font-medium truncate cursor-default'}
      title="더블클릭하여 편집"
    >
      {title}
    </span>
  )
}
