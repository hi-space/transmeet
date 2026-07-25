'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { isIOS, supportsSystemAudio } from '@/lib/platform'

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
  const muteRef = useRef<GainNode | null>(null)
  const resumeHandlerRef = useRef<(() => void) | null>(null) // iOS 중단 복구 리스너 해제
  const streamRef = useRef<MediaStream | null>(null) // mic stream
  const sysStreamRef = useRef<MediaStream | null>(null) // system audio stream
  const samplesRef = useRef<Float32Array[]>([])
  const workerRef = useRef<Worker | null>(null)
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

      const ios = isIOS()

      // ── iOS: 시스템 오디오 미지원 → 마이크로 폴백 ─────────────────────────
      // getDisplayMedia가 없어 TypeError로 죽는 대신 마이크만이라도 녹음시킨다.
      let effectiveSource = audioSource
      if (effectiveSource !== 'mic' && !supportsSystemAudio()) {
        effectiveSource = 'mic'
      }

      // ── AudioContext 생성 시점: 플랫폼별로 다름 ───────────────────────────
      // 데스크톱: getDisplayMedia/getUserMedia는 async라 이후엔 user gesture가
      //   만료되므로, running 상태로 시작하려면 gesture 안에서 먼저 만들어야 함.
      // iOS: 반대로 getUserMedia가 오디오 세션을 재구성하기 때문에, 그보다 먼저
      //   만든 AudioContext는 createMediaStreamSource가 무음만 내보낸다.
      //   → 반드시 마이크를 먼저 잡고 그 다음에 AudioContext를 만든다.
      const createCtx = async () => {
        // 구형 iOS Safari는 prefixed webkitAudioContext만 노출한다
        const Ctor: typeof AudioContext =
          typeof AudioContext !== 'undefined'
            ? AudioContext
            : (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        const c = new Ctor()
        if (c.state !== 'running') await c.resume()
        audioContextRef.current = c
        return c
      }

      let ctx: AudioContext | null = null
      if (!ios) ctx = await createCtx()

      // ── 마이크 스트림 획득 ────────────────────────────────────────────────
      if (effectiveSource === 'mic' || effectiveSource === 'both') {
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
      if (effectiveSource === 'system' || effectiveSource === 'both') {
        const displayStream = await navigator.mediaDevices.getDisplayMedia({
          video: { width: 1, height: 1, frameRate: 1 }, // 오디오만 필요하므로 최소 해상도
          audio: true,
        })

        if (displayStream.getAudioTracks().length === 0) {
          displayStream.getTracks().forEach((t) => t.stop())
          throw new Error(
            '시스템 오디오를 가져올 수 없습니다. 화면 공유 시 "오디오 공유"를 체크해 주세요.'
          )
        }
        sysStream = displayStream
        sysStreamRef.current = sysStream
      }

      // iOS: 스트림 확보 후에 AudioContext 생성 (위 주석 참고)
      if (!ctx) ctx = await createCtx()

      // resume()이 무시된 경우(자동재생 정책·세션 중단) 무음 녹음을 방지하고 즉시 알린다
      if (ctx.state !== 'running') {
        throw new Error(
          '오디오 세션을 시작할 수 없습니다. 다른 앱의 오디오/통화를 종료한 뒤 다시 시도해 주세요.'
        )
      }

      // ── AudioContext 노드 구성 ────────────────────────────────────────────

      const processor = ctx.createScriptProcessor(SCRIPT_PROCESSOR_BUFFER_SIZE, 1, 1)
      processorRef.current = processor
      processor.onaudioprocess = (e) => {
        samplesRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0)))
      }
      // ScriptProcessor는 destination까지 연결돼 있어야 onaudioprocess가 발생하지만,
      // 직접 연결하면 캡처한 오디오가 스피커로 나가 iOS에서 하울링이 생긴다.
      // gain 0 노드를 경유해 그래프는 유지하면서 출력만 죽인다.
      const mute = ctx.createGain()
      mute.gain.value = 0
      muteRef.current = mute
      processor.connect(mute)
      mute.connect(ctx.destination)

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
        // video track은 stop() 시에 sysStreamRef.current?.getTracks()로 일괄 정리
        // 녹화 중 video track 중단 시 Chrome에서 stream 전체가 비활성화되어 묵음이 됨
      }

      const worker = new Worker(new URL('../workers/audio-timer.worker.ts', import.meta.url))
      worker.onmessage = (e) => {
        if (e.data.type === 'tick') flushChunk()
      }
      worker.postMessage({ type: 'start', interval: chunkDurationMs })
      workerRef.current = worker
      levelAnimRef.current = requestAnimationFrame(updateLevel)
      setIsRecording(true)

      // ── iOS 오디오 세션 중단 복구 ─────────────────────────────────────────
      // 전화 수신 / 다른 앱 오디오 / 화면 잠금 시 iOS는 AudioContext를
      // interrupted·suspended 상태로 내려버리고 자동으로 되돌리지 않는다.
      // 앱이 포그라운드로 돌아올 때 resume해서 녹음이 조용히 죽는 걸 막는다.
      if (ios) {
        const onStateChange = () => {
          const c = audioContextRef.current
          if (!c) return
          if (c.state !== 'running' && document.visibilityState === 'visible') {
            c.resume().catch(() => {})
          }
        }
        ctx.addEventListener('statechange', onStateChange)
        document.addEventListener('visibilitychange', onStateChange)
        resumeHandlerRef.current = () => {
          ctx?.removeEventListener('statechange', onStateChange)
          document.removeEventListener('visibilitychange', onStateChange)
        }
      }
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
      // 부분적으로 획득한 리소스 정리
      resumeHandlerRef.current?.()
      resumeHandlerRef.current = null
      processorRef.current?.disconnect()
      processorRef.current = null
      muteRef.current?.disconnect()
      muteRef.current = null
      audioContextRef.current?.close()
      audioContextRef.current = null
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
      sysStreamRef.current?.getTracks().forEach((t) => t.stop())
      sysStreamRef.current = null
    }
  }, [audioSource, chunkDurationMs, flushChunk, updateLevel])

  const stop = useCallback(() => {
    flushChunk()

    if (workerRef.current) {
      workerRef.current.postMessage({ type: 'stop' })
      workerRef.current.terminate()
      workerRef.current = null
    }
    if (levelAnimRef.current) {
      cancelAnimationFrame(levelAnimRef.current)
      levelAnimRef.current = null
    }

    resumeHandlerRef.current?.()
    resumeHandlerRef.current = null

    processorRef.current?.disconnect()
    processorRef.current = null
    muteRef.current?.disconnect()
    muteRef.current = null
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
      if (workerRef.current) {
        workerRef.current.postMessage({ type: 'stop' })
        workerRef.current.terminate()
      }
      if (levelAnimRef.current) cancelAnimationFrame(levelAnimRef.current)
      resumeHandlerRef.current?.()
      processorRef.current?.disconnect()
      muteRef.current?.disconnect()
      analyserRef.current?.disconnect()
      audioContextRef.current?.close()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      sysStreamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return { isRecording, audioLevel, error, start, stop }
}
