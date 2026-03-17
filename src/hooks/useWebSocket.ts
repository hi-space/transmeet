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
  const keepaliveRef = useRef<ReturnType<typeof setInterval> | null>(null)
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
      setStatus('error')
      return
    }

    // Clear any pending reconnect timer to avoid duplicate connections
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    // Clean up any existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close(1000)
      wsRef.current = null
    }

    shouldReconnectRef.current = true

    const url = meetingIdRef.current
      ? `${endpoint}?meetingId=${encodeURIComponent(meetingIdRef.current)}`
      : endpoint

    console.log('[WS] Connecting to', url)
    setStatus('connecting')
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      // Reset counter only on successful connection (preserves backoff on reconnect)
      reconnectCountRef.current = 0
      console.log('[WS] Connected')
      setStatus('connected')
      // Keepalive: send ping every 30s to prevent API Gateway idle timeout
      keepaliveRef.current = setInterval(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ action: 'ping' }))
        }
      }, 30000)
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
      if (keepaliveRef.current) {
        clearInterval(keepaliveRef.current)
        keepaliveRef.current = null
      }
      wsRef.current = null

      // Normal close (code 1000) or explicitly disconnected
      if (!shouldReconnectRef.current || event.code === 1000) {
        setStatus('disconnected')
        return
      }

      // Unexpected close — attempt reconnect with exponential backoff
      if (reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectCountRef.current++
        const delay = RECONNECT_DELAY_MS * reconnectCountRef.current
        console.warn(
          '[WS] Unexpected close (code=%d), reconnecting in %dms (attempt %d/%d)',
          event.code,
          delay,
          reconnectCountRef.current,
          MAX_RECONNECT_ATTEMPTS
        )
        setStatus('connecting')
        reconnectTimerRef.current = setTimeout(connect, delay)
      } else {
        console.error('[WS] Max reconnect attempts reached, giving up')
        setStatus('error')
        shouldReconnectRef.current = false
      }
    }

    ws.onerror = () => {
      // onerror always precedes onclose; let onclose handle state transition
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
    (
      audioBase64: string,
      speaker: string = 'speaker1',
      sourceLang?: string,
      targetLang?: string,
      modelId?: string
    ): boolean => {
      const ws = wsRef.current
      const readyState = ws?.readyState
      if (!ws || readyState !== WebSocket.OPEN) {
        // Reconnect if not already connecting
        if (readyState !== WebSocket.CONNECTING) {
          console.warn('[WS] sendAudio: not connected, attempting reconnect...')
          connect()
        } else {
          console.warn('[WS] sendAudio blocked: still connecting')
        }
        return false
      }

      ws.send(
        JSON.stringify({
          action: 'sendAudio',
          audioData: audioBase64,
          meetingId: meetingIdRef.current,
          speaker,
          ...(sourceLang && sourceLang !== 'auto' && { sourceLang }),
          ...(targetLang && { targetLang }),
          ...(modelId && { modelId }),
        })
      )
      console.log('[WS] sendAudio sent: b64_len=%d', audioBase64.length)
      return true
    },
    [connect]
  )

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      shouldReconnectRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (keepaliveRef.current) clearInterval(keepaliveRef.current)
      wsRef.current?.close(1000)
    }
  }, [])

  return { status, connect, disconnect, sendAudio }
}
