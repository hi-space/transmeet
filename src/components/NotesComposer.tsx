'use client'

import type { Ref } from 'react'
import Icon from '@/components/ui/Icon'

interface Props {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  isPending?: boolean
  disabled?: boolean
  formRef?: Ref<HTMLFormElement>
}

export default function NotesComposer({
  value,
  onChange,
  onSend,
  onStop,
  isPending = false,
  disabled = false,
  formRef,
}: Props) {
  return (
    <form
      ref={formRef}
      aria-label="내 메모 작성"
      className="shrink-0 border-t border-subtle bg-panel p-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (value.trim() && !isPending && !disabled) onSend()
      }}
    >
      <div className="flex items-end gap-2 rounded-xl border border-subtle bg-subtle p-2 transition-colors focus-within:border-accent-400 focus-within:ring-2 focus-within:ring-accent-500/10">
        <textarea
          aria-label="내 메모 입력"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) return
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (value.trim() && !isPending && !disabled) onSend()
            }
          }}
          disabled={isPending || disabled}
          placeholder="한글로 입력하면 영어로 번역됩니다"
          rows={2}
          className="block min-h-12 min-w-0 flex-1 resize-none bg-transparent px-1 py-1 text-sm leading-relaxed text-ink placeholder:text-slate-500 focus:outline-none disabled:opacity-60 dark:placeholder:text-slate-400"
        />
        {isPending ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="음성 재생 중지"
            title="음성 재생 중지"
            className="ui-icon-button min-h-10 min-w-10 text-red-600 dark:text-red-400"
          >
            <Icon name="stop" className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!value.trim() || disabled}
            aria-label="메모 보내기"
            title="Enter로 보내기 · Shift + Enter로 줄바꿈"
            className="ui-icon-button ui-button-tinted min-h-10 min-w-10"
          >
            <Icon name="send" className="h-4 w-4" />
          </button>
        )}
      </div>
    </form>
  )
}
