'use client'

import { useId } from 'react'
import Icon, { type IconName } from './Icon'

export interface TabItem<T extends string> {
  value: T
  label: string
  icon?: IconName
  badge?: boolean
  panelId?: string
}

interface Props<T extends string> {
  label: string
  items: TabItem<T>[]
  value: T
  onChange: (value: T) => void
  className?: string
  panelId?: string
}

export default function Tabs<T extends string>({
  label,
  items,
  value,
  onChange,
  className = '',
  panelId,
}: Props<T>) {
  const id = useId()

  return (
    <div role="tablist" aria-label={label} className={`ui-tabs ${className}`}>
      {items.map((item, index) => (
        <button
          key={item.value}
          id={`${id}-${item.value}`}
          type="button"
          role="tab"
          aria-selected={value === item.value}
          aria-controls={item.panelId ?? panelId}
          tabIndex={value === item.value ? 0 : -1}
          className="ui-tab"
          onClick={() => onChange(item.value)}
          onKeyDown={(event) => {
            let next = index
            if (event.key === 'ArrowRight') next = (index + 1) % items.length
            else if (event.key === 'ArrowLeft') next = (index - 1 + items.length) % items.length
            else if (event.key === 'Home') next = 0
            else if (event.key === 'End') next = items.length - 1
            else return
            event.preventDefault()
            onChange(items[next].value)
            document.getElementById(`${id}-${items[next].value}`)?.focus()
          }}
        >
          {item.icon && <Icon name={item.icon} className="h-3.5 w-3.5" />}
          <span>{item.label}</span>
          {item.badge && value !== item.value && (
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-500">
              <span className="sr-only">새 내용</span>
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
