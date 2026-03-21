'use client'

import { useState, useRef, useCallback, useEffect } from 'react'

// Default: 700ms for Transcribe streaming (feed continuously); 2000ms for Whisper (better context)
// CloudFront→ALB→Fargate supports large frames, so 2000ms chunks are safe for Whisper
const CHUNK_DURATION_MS = 700
const SCRIPT_PROCESSOR_BUFFER_SIZE = 4096

// Downsample to 16kHz (Whisper's native rate) using linear interpolation
function downsampleTo16k(samples: Float32Array, inputRate: number): Float32Array {
  if (inputRate === 16000) return samples
  const ratio = inputRate / 16000
  const length = Math.floor(samples.length / ratio)
  const result = new Float32Array(length)
  for (let i = 0; i < length; i++) {
    const src = i * ratio
    const lo = Math.floor(src)
    const hi = Math.min(lo + 1, samples.length - 1)
    result[i] = samples[lo] + (src - lo) * (samples[hi] - samples[lo])
  }
  return result
}

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
  audioSource?: 'mic' | 'system' | 'both'
}

export function useAudioCapture({
  onChunk,
  chunkDurationMs = CHUNK_DURATION_MS,
  audioSource = 'mic',
}: UseAudioCaptureOptions) {
  const [isRecording, setIsRecording] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null) // mic stream
  const sysStreamRef = useRef<MediaStream | null>(null) // system audio stream
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

    const inputRate = audioContextRef.current?.sampleRate ?? 48000
    const resampled = downsampleTo16k(combined, inputRate)
    const wavBuffer = encodeWav(resampled, 16000)
    const b64 = arrayBufferToBase64(wavBuffer)
    onChunkRef.current(b64)
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
      let micStream: MediaStream | null = null
      let sysStream: MediaStream | null = null

      // ── 마이크 스트림 획득 ────────────────────────────────────────────────
      if (audioSource === 'mic' || audioSource === 'both') {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
        streamRef.current = micStream
      }

      // ── 시스템 오디오 스트림 획득 ─────────────────────────────────────────
      if (audioSource === 'system' || audioSource === 'both') {
        // video:false는 일부 브라우저에서 미지원 → video:true 후 track 즉시 중단
        const displayStream = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: true,
        })
        displayStream.getVideoTracks().forEach((t) => t.stop())

        const audioTracks = displayStream.getAudioTracks()
        if (audioTracks.length === 0) {
          displayStream.getTracks().forEach((t) => t.stop())
          throw new Error(
            '시스템 오디오를 가져올 수 없습니다. 화면 공유 시 "오디오 공유"를 체크해 주세요.'
          )
        }
        sysStream = new MediaStream(audioTracks)
        sysStreamRef.current = sysStream
      }

      // ── AudioContext + 노드 구성 ──────────────────────────────────────────
      const ctx = new AudioContext()
      audioContextRef.current = ctx

      const processor = ctx.createScriptProcessor(SCRIPT_PROCESSOR_BUFFER_SIZE, 1, 1)
      processorRef.current = processor
      processor.onaudioprocess = (e) => {
        samplesRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0)))
      }
      processor.connect(ctx.destination)

      // 레벨 시각화용 analyser (mic 우선, system-only면 system)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyserRef.current = analyser

      if (micStream) {
        const micSource = ctx.createMediaStreamSource(micStream)
        micSource.connect(analyser) // 레벨 표시
        micSource.connect(processor) // PCM 수집
      }

      if (sysStream) {
        const sysSource = ctx.createMediaStreamSource(sysStream)
        if (!micStream) sysSource.connect(analyser) // system-only: analyser에도 연결
        sysSource.connect(processor) // PCM 수집 (AudioContext가 자동 믹싱)
      }

      chunkTimerRef.current = setInterval(flushChunk, chunkDurationMs)
      levelAnimRef.current = requestAnimationFrame(updateLevel)
      setIsRecording(true)
    } catch (err) {
      // getDisplayMedia 취소(NotAllowedError)와 명시적 에러 메시지 구분
      const msg =
        err instanceof Error && err.message.includes('시스템 오디오')
          ? err.message
          : err instanceof DOMException && err.name === 'NotAllowedError'
            ? audioSource === 'mic'
              ? '마이크 접근이 거부되었습니다. 브라우저 설정에서 허용해 주세요.'
              : '화면/오디오 공유가 거부되었습니다.'
            : err instanceof Error
              ? err.message
              : '오디오를 시작할 수 없습니다.'
      setError(msg)
      // 부분적으로 획득한 스트림 정리
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      sysStreamRef.current?.getTracks().forEach((t) => t.stop())
      sysStreamRef.current = null
    }
  }, [audioSource, chunkDurationMs, flushChunk, updateLevel])

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
    sysStreamRef.current?.getTracks().forEach((t) => t.stop())
    sysStreamRef.current = null

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
      sysStreamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return { isRecording, audioLevel, error, start, stop }
}
