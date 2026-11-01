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
        // 화면과 같은 규칙: 화자가 한 명뿐이면 "화자 1:" 을 줄마다 붙이지 않는다
        const showSpeakers = new Set(conversation.map((message) => message.speaker)).size > 1
        const body = conversation
          .map((message) => {
            const time = new Date(message.timestamp).toLocaleTimeString('ko-KR', {
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })
            const who = showSpeakers ? `${getSpeakerLabel(message.speaker)}: ` : ''
            return `[${time}] ${who}${message.original.trim()}`
          })
          .join('\n')
        return copyText(header + body)
      }}
    />
  )
}
