'use client'

import { useState, useEffect } from 'react'

export interface Settings {
  sourceLang: 'ko' | 'en' | 'auto'
  targetLang: 'ko' | 'en'
  sttProvider: 'whisper' | 'transcribe'
  ttsAutoPlay: boolean
  pollyEngine: 'generative' | 'neural' | 'standard'
  pollyVoiceId: string
  autoSummarizeInterval: 0 | 1 | 2 | 5 | 10 // minutes; 0 = off
  translationModel: string
}

const DEFAULT: Settings = {
  sourceLang: 'en',
  targetLang: 'ko',
  sttProvider: 'whisper',
  ttsAutoPlay: true,
  pollyEngine: 'generative',
  pollyVoiceId: 'Ruth',
  autoSummarizeInterval: 2,
  translationModel: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
}

const STORAGE_KEY = 'transmeet-settings'

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(DEFAULT)

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) setSettings({ ...DEFAULT, ...(JSON.parse(raw) as Partial<Settings>) })
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
