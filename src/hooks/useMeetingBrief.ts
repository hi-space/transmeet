'use client'

import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { Meeting, MeetingBrief } from '@/types/meeting'

const BRIEF_MESSAGE_INTERVAL = 20

export function useMeetingBrief(
  meeting: Meeting | undefined,
  enabled: boolean,
  onUpdate: (meetingId: string, brief: MeetingBrief) => void
) {
  const pending = useRef(new Set<string>())
  const attempted = useRef(new Map<string, string>())
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!enabled || !meeting || pending.current.has(meeting.id)) return
    const messages = meeting.messages
      .filter((message) => message.original.trim())
      .map(({ id, original }) => ({ id, original }))
    if (messages.length - (meeting.briefMessageCount ?? 0) < BRIEF_MESSAGE_INTERVAL) return

    const meetingId = meeting.id
    const snapshot = `${messages.length}:${messages[messages.length - 1].id}`
    // Do not retry a failed snapshot on every translation or summary render.
    if (attempted.current.get(meetingId) === snapshot) return
    attempted.current.set(meetingId, snapshot)
    pending.current.add(meetingId)
    setRevision((value) => value + 1)

    api.meetings
      .brief(meetingId, messages)
      .then((brief) => {
        if (brief.brief?.trim()) onUpdate(meetingId, brief)
      })
      .catch(() => {
        // Keep the previous brief visible; retry when another message arrives.
      })
      .finally(() => {
        pending.current.delete(meetingId)
        // Catch up if another 20 messages arrived while generation was in flight.
        setRevision((value) => value + 1)
      })
  }, [meeting, enabled, onUpdate, revision])

  return !!meeting && pending.current.has(meeting.id)
}
