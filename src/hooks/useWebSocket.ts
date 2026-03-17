'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import type { WsStatus, WsServerMessage } from '@/lib/websocket'

const RECONNECT_DELAY_MS = 2000
const MAX_RECONNECT_ATTEMPTS = 5

interface UseWebSocketOptions {
  meetingId?: string
  onMessage: (msg: WsServerMessage) => void
}

export function useWebSocket({ meetingId, onMessage }: UseWebSocketOptions) {
  const [status, setStatus] = useState<WsStatus>('disconnected')

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectCountRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onMessageRef = useRef(onMessage)
  const meetingIdRef = useRef(meetingId)
  // Track whether the current session should reconnect on unexpected close
  const shouldReconnectRef = useRef(false)

  useEffect(() => {
    onMessageRef.current = onMessage
  }, [onMessage])

  useEffect(() => {
    meetingIdRef.current = meetingId
  }, [meetingId])

  const connect = useCallback(() => {
    const endpoint = process.env.NEXT_PUBLIC_WS_ENDPOINT
    if (!endpoint) {
      console.warn('[WS] NEXT_PUBLIC_WS_ENDPOINT is not set')
      setStatus('error')
      return
    }

    // Clean up any existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close(1000)
      wsRef.current = null
    }

    reconnectCountRef.current = 0
    shouldReconnectRef.current = true

    const url = meetingIdRef.current
      ? `${endpoint}?meetingId=${encodeURIComponent(meetingIdRef.current)}`
      : endpoint

    setStatus('connecting')
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectCountRef.current = 0
      setStatus('connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string) as WsServerMessage
        onMessageRef.current(msg)
      } catch (err) {
        console.error('[WS] Failed to parse message:', err)
      }
    }

    ws.onclose = (event) => {
      wsRef.current = null

      // Normal close (code 1000) or explicitly disconnected
      if (!shouldReconnectRef.current || event.code === 1000) {
        setStatus('disconnected')
        return
      }

      // Unexpected close — attempt reconnect with backoff
      if (reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectCountRef.current++
        const delay = RECONNECT_DELAY_MS * reconnectCountRef.current
        console.log(
          `[WS] Reconnecting (attempt ${reconnectCountRef.current}/${MAX_RECONNECT_ATTEMPTS}) in ${delay}ms`
        )
        setStatus('connecting')
        reconnectTimerRef.current = setTimeout(connect, delay)
      } else {
        setStatus('error')
        shouldReconnectRef.current = false
      }
    }

    ws.onerror = () => {
      // onerror always precedes onclose, so let onclose handle state
      console.error('[WS] Connection error')
    }
  }, []) // stable — all deps via refs

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false
    reconnectCountRef.current = MAX_RECONNECT_ATTEMPTS

    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    if (wsRef.current) {
      wsRef.current.close(1000)
      wsRef.current = null
    }

    setStatus('disconnected')
  }, [])

  const sendAudio = useCallback(
    (audioBase64: string, speaker: string = 'speaker1'): boolean => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return false

      wsRef.current.send(
        JSON.stringify({
          action: 'sendAudio',
          audioData: audioBase64,
          meetingId: meetingIdRef.current,
          speaker,
        })
      )
      return true
    },
    []
  )

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      shouldReconnectRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      wsRef.current?.close(1000)
    }
  }, [])

  return { status, connect, disconnect, sendAudio }
}
