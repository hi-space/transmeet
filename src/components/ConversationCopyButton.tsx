'use client'

import CopyButton from '@/components/CopyButton'
import { copyText } from '@/lib/clipboard'
import { getSpeakerLabel, isVoiceSpeaker } from '@/lib/speakers'
import type { Message } from '@/types/meeting'

interface Props {
  messages: Message[]
  meetingTitle?: string
  disabled?: boolean
}

export default function ConversationCopyButton({ messages, meetingTitle, disabled }: Props) {
  const conversation = messages.filter((message) => isVoiceSpeaker(message.speaker))

  return (
    <CopyButton
      label="대화 복사"
      compact
      title="전체 대화 원문 복사"
      disabled={disabled || conversation.length === 0}
      onCopy={() => {
        const header = meetingTitle ? `${meetingTitle}\n\n` : ''
        const body = conversation
          .map((message) => {
            const time = new Date(message.timestamp).toLocaleTimeString('ko-KR', {
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })
            return `[${time}] ${getSpeakerLabel(message.speaker)}: ${message.original.trim()}`
          })
          .join('\n')
        return copyText(header + body)
      }}
    />
  )
}
