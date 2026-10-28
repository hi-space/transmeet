'use client'

import type { RefObject } from 'react'
import CopyButton from '@/components/CopyButton'
import { copyRichText, copyText, type ClipboardContent } from '@/lib/clipboard'
import { createClipboardContent } from '@/lib/clipboardContent'

interface Props {
  markdown: string
  label?: string
  contentRef?: RefObject<HTMLDivElement>
  content?: ClipboardContent
  compact?: boolean
}

export default function MarkdownCopyButtons({
  markdown,
  label = '서식 복사',
  contentRef,
  content,
  compact = false,
}: Props) {
  return (
    <>
      <CopyButton
        label={label}
        compact={compact}
        title="굵게, 목록, 링크 등의 서식을 함께 복사 (Slack·문서용)"
        onCopy={() => {
          if (content) return copyRichText(content)
          if (!contentRef?.current) return Promise.reject(new Error('복사할 내용이 없습니다'))
          return copyRichText(createClipboardContent(contentRef.current))
        }}
      />
      <CopyButton
        label="마크다운 복사"
        compact={compact}
        compactLabel="MD"
        title="마크다운 원문 복사"
        onCopy={() => copyText(markdown)}
      />
    </>
  )
}
