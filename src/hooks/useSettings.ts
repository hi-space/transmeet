'use client'

import { useState, useEffect } from 'react'

export interface Settings {
  sourceLang: 'ko' | 'en' | 'auto'
  targetLang: 'ko' | 'en'
  audioSource: 'mic' | 'system' | 'both'
  sttProvider: 'whisper' | 'transcribe'
  ttsAutoPlay: boolean
  pollyEngine: 'generative' | 'neural' | 'standard'
  pollyVoiceId: string
  translationModel: string
  translationTiming: 'sentence' | 'realtime' | 'manual'
  partialTranslationMode: 'realtime' | 'sentence'
  partialThrottleMs: 500 | 1000 | 1500 | 2000 | 3000
  silenceTimeout: 3000 | 5000 | 10000 | 20000
  translationOutputMode: 'stream' | 'complete'
  autoSummarizeMessageCount: number
}

// engine → voice 지원 여부 검증용 (SettingsPanel과 동일 데이터)
const LANG_VOICE_ENGINES: Record<'en' | 'ko', string[]> = {
  en: ['generative', 'neural', 'standard'],
  ko: ['neural'],
}

const LANG_DEFAULT_VOICE: Record<'en' | 'ko', { id: string; engine: Settings['pollyEngine'] }> = {
  en: { id: 'Ruth', engine: 'generative' },
  ko: { id: 'Seoyeon', engine: 'neural' },
}

const DEFAULT: Settings = {
  sourceLang: 'en',
  targetLang: 'ko',
  audioSource: 'mic',
  sttProvider: 'whisper',
  ttsAutoPlay: true,
  pollyEngine: 'generative',
  pollyVoiceId: 'Ruth',
  translationModel: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
  translationTiming: 'realtime',
  partialTranslationMode: 'sentence',
  partialThrottleMs: 1500,
  silenceTimeout: 10000,
  translationOutputMode: 'stream',
  autoSummarizeMessageCount: 10,
}

const STORAGE_KEY = 'transmeet-settings'

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(DEFAULT)

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const loaded = { ...DEFAULT, ...(JSON.parse(raw) as Partial<Settings>) }
        // engine이 sourceLang을 지원하지 않으면 기본값으로 자동 수정
        const ttsLang = loaded.sourceLang === 'auto' ? 'en' : loaded.sourceLang
        const supportedEngines = LANG_VOICE_ENGINES[ttsLang]
        if (!supportedEngines.includes(loaded.pollyEngine)) {
          const d = LANG_DEFAULT_VOICE[ttsLang]
          loaded.pollyEngine = d.engine
          loaded.pollyVoiceId = d.id
          localStorage.setItem(STORAGE_KEY, JSON.stringify(loaded))
        }
        setSettings(loaded)
      }
    } catch {
      // ignore corrupt storage
    }
  }, [])

  const updateSettings = (patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch }
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        // ignore
      }
      return next
    })
  }

  return { settings, updateSettings }
}
