'use client'

import { useState, useEffect } from 'react'
import {
  DEFAULT_STT_CHUNK_SECONDS,
  normalizeSttChunkSeconds,
  normalizeSttProvider,
  type SttChunkSeconds,
  type SttProvider,
} from '@/lib/sttProviders'
import {
  DEFAULT_TRANSLATION_MODEL_ID,
  normalizeTranslationModel,
  type TranslationModelId,
} from '@/lib/translationModels'
import { normalizeSpeechLanguages, type SpeechLanguages } from '@/lib/speechLanguages'

export interface Settings extends SpeechLanguages {
  audioSource: 'mic' | 'system' | 'both'
  sttProvider: SttProvider
  sttChunkSeconds: SttChunkSeconds
  ttsAutoPlay: boolean
  pollyEngine: 'generative' | 'neural' | 'standard'
  pollyVoiceId: string
  translationModel: TranslationModelId
  translationTiming: 'sentence' | 'realtime' | 'manual'
  partialTranslationMode: 'realtime' | 'sentence'
  partialThrottleMs: 500 | 1000 | 1500 | 2000 | 3000
  silenceTimeout: 3000 | 5000 | 10000 | 20000
  /** 말풍선 하나에 합칠 최대 문장 수. 1이면 문장마다 새 말풍선 */
  maxSentencesPerBubble: 1 | 2 | 3 | 5
  autoSummarizeMessageCount: number
  /** 음성 입력 말풍선 글자 크기 — 화면 공유 시 크게 키운다 */
  voiceFontSize: 'compact' | 'md' | 'lg' | 'xl'
  /** "미팅 종료" 때 요약과 녹음 다운로드 링크를 로그인 이메일로 보낸다 (SES 수신 주소 인증 필요) */
  emailOnEnd: boolean
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
  translationTarget: 'ko',
  audioSource: 'mic',
  sttProvider: 'transcribe',
  sttChunkSeconds: DEFAULT_STT_CHUNK_SECONDS,
  ttsAutoPlay: true,
  pollyEngine: 'generative',
  pollyVoiceId: 'Ruth',
  translationModel: DEFAULT_TRANSLATION_MODEL_ID,
  translationTiming: 'realtime',
  partialTranslationMode: 'sentence',
  partialThrottleMs: 1500,
  silenceTimeout: 10000,
  maxSentencesPerBubble: 3,
  autoSummarizeMessageCount: 10,
  voiceFontSize: 'compact',
  emailOnEnd: false,
}

const STORAGE_KEY = 'transmeet-settings'

export function useSettings() {
  const [settings, setSettings] = useState<Settings>(DEFAULT)

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const saved: unknown = JSON.parse(raw)
        if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return
        const { targetLang: _legacyTarget, ...savedSettings } = saved as Record<string, unknown>
        const loaded: Settings = {
          ...DEFAULT,
          ...(savedSettings as Partial<Settings>),
          ...normalizeSpeechLanguages(saved),
        }
        // 이전 모델 선택도 현재 제공하는 글로벌 추론 프로필로 정리한다.
        loaded.translationModel = normalizeTranslationModel(loaded.translationModel)
        loaded.sttProvider = normalizeSttProvider(loaded.sttProvider)
        loaded.sttChunkSeconds = normalizeSttChunkSeconds(loaded.sttChunkSeconds)
        // engine이 sourceLang을 지원하지 않으면 기본값으로 자동 수정
        const ttsLang = loaded.sourceLang === 'auto' ? 'en' : loaded.sourceLang
        const supportedEngines = LANG_VOICE_ENGINES[ttsLang]
        if (!supportedEngines.includes(loaded.pollyEngine)) {
          const d = LANG_DEFAULT_VOICE[ttsLang]
          loaded.pollyEngine = d.engine
          loaded.pollyVoiceId = d.id
        }
        setSettings(loaded)
        localStorage.setItem(STORAGE_KEY, JSON.stringify(loaded))
      }
    } catch {
      // ignore corrupt storage
    }
  }, [])

  const updateSettings = (patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = {
        ...prev,
        ...patch,
        ...normalizeSpeechLanguages({ ...prev, ...patch }),
      }
      next.translationModel = normalizeTranslationModel(next.translationModel)
      next.sttProvider = normalizeSttProvider(next.sttProvider)
      next.sttChunkSeconds = normalizeSttChunkSeconds(next.sttChunkSeconds)

      // sourceLang 이 바뀌면 TTS 목소리/엔진을 해당 언어 기본값으로 되돌린다.
      // (ko 는 Seoyeon/neural 만 지원하므로 en 설정이 그대로 남으면 재생이 실패한다)
      if (patch.sourceLang && patch.sourceLang !== prev.sourceLang) {
        const lang = patch.sourceLang === 'auto' ? 'en' : patch.sourceLang
        const d = LANG_DEFAULT_VOICE[lang]
        next.pollyVoiceId = patch.pollyVoiceId ?? d.id
        next.pollyEngine = patch.pollyEngine ?? d.engine
      }

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
