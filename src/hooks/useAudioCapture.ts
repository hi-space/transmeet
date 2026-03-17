'use client'

import { useState, useRef, useCallback, useEffect } from 'react'

// 500ms chunks — short enough for low latency, long enough for Whisper accuracy
const CHUNK_DURATION_MS = 500
const SCRIPT_PROCESSOR_BUFFER_SIZE = 4096

function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const dataLen = samples.length * 2 // 16-bit = 2 bytes per sample
  const buffer = new ArrayBuffer(44 + dataLen)
  const view = new DataView(buffer)

  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i))
  }

  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + dataLen, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true) // PCM subchunk size
  view.setUint16(20, 1, true) // PCM format
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // byte rate
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  writeStr(36, 'data')
  view.setUint32(40, dataLen, true)

  let offset = 44
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }

  return buffer
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

interface UseAudioCaptureOptions {
  onChunk: (wavBase64: string) => void
  chunkDurationMs?: number
}

export function useAudioCapture({
  onChunk,
  chunkDurationMs = CHUNK_DURATION_MS,
}: UseAudioCaptureOptions) {
  const [isRecording, setIsRecording] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const samplesRef = useRef<Float32Array[]>([])
  const chunkTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const levelAnimRef = useRef<number | null>(null)
  const onChunkRef = useRef(onChunk)

  useEffect(() => {
    onChunkRef.current = onChunk
  }, [onChunk])

  const flushChunk = useCallback(() => {
    if (samplesRef.current.length === 0) return

    const totalLength = samplesRef.current.reduce((acc, a) => acc + a.length, 0)
    if (totalLength < 1600) return // skip very short chunks (< ~100ms at 16kHz)

    const combined = new Float32Array(totalLength)
    let offset = 0
    for (const arr of samplesRef.current) {
      combined.set(arr, offset)
      offset += arr.length
    }
    samplesRef.current = []

    const sampleRate = audioContextRef.current?.sampleRate ?? 44100
    const wavBuffer = encodeWav(combined, sampleRate)
    onChunkRef.current(arrayBufferToBase64(wavBuffer))
  }, [])

  const updateLevel = useCallback(() => {
    if (!analyserRef.current) return
    const data = new Uint8Array(analyserRef.current.frequencyBinCount)
    analyserRef.current.getByteFrequencyData(data)
    const avg = data.reduce((a, b) => a + b, 0) / data.length
    setAudioLevel(avg / 255)
    levelAnimRef.current = requestAnimationFrame(updateLevel)
  }, [])

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      streamRef.current = stream

      const ctx = new AudioContext()
      audioContextRef.current = ctx
      const source = ctx.createMediaStreamSource(stream)

      // Analyser for level visualization
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyserRef.current = analyser
      source.connect(analyser)

      // ScriptProcessorNode to collect PCM samples
      const processor = ctx.createScriptProcessor(SCRIPT_PROCESSOR_BUFFER_SIZE, 1, 1)
      processorRef.current = processor
      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0)
        samplesRef.current.push(new Float32Array(inputData))
      }
      source.connect(processor)
      processor.connect(ctx.destination)

      chunkTimerRef.current = setInterval(flushChunk, chunkDurationMs)
      levelAnimRef.current = requestAnimationFrame(updateLevel)
      setIsRecording(true)
    } catch (err) {
      const msg =
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? '마이크 접근이 거부되었습니다. 브라우저 설정에서 허용해 주세요.'
          : err instanceof Error
            ? err.message
            : '마이크를 사용할 수 없습니다.'
      setError(msg)
    }
  }, [chunkDurationMs, flushChunk, updateLevel])

  const stop = useCallback(() => {
    flushChunk()

    if (chunkTimerRef.current) {
      clearInterval(chunkTimerRef.current)
      chunkTimerRef.current = null
    }
    if (levelAnimRef.current) {
      cancelAnimationFrame(levelAnimRef.current)
      levelAnimRef.current = null
    }

    processorRef.current?.disconnect()
    processorRef.current = null
    analyserRef.current?.disconnect()
    analyserRef.current = null

    audioContextRef.current?.close()
    audioContextRef.current = null

    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null

    samplesRef.current = []
    setAudioLevel(0)
    setIsRecording(false)
  }, [flushChunk])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (chunkTimerRef.current) clearInterval(chunkTimerRef.current)
      if (levelAnimRef.current) cancelAnimationFrame(levelAnimRef.current)
      processorRef.current?.disconnect()
      analyserRef.current?.disconnect()
      audioContextRef.current?.close()
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return { isRecording, audioLevel, error, start, stop }
}
