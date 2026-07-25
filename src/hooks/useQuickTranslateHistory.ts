'use client'

import { useState, useCallback, useEffect } from 'react'

export interface TranslationRecord {
  id: string
  sourceText: string
  targetText: string
  sourceLang?: 'ko' | 'en'
  targetLang?: 'ko' | 'en'
  audioData?: string
  createdAt: string
}

// 방향 설정 이전 버전은 koreanText/englishText 로 저장했다 (항상 ko→en)
type LegacyRecord = TranslationRecord & { koreanText?: string; englishText?: string }

const STORAGE_KEY = 'transmeet-quick-translate-history'
const MAX_ITEMS = 20

function loadHistory(): TranslationRecord[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as LegacyRecord[]
    return parsed.map(({ koreanText, englishText, ...r }) => ({
      ...r,
      sourceText: r.sourceText ?? koreanText ?? '',
      targetText: r.targetText ?? englishText ?? '',
      sourceLang: r.sourceLang ?? (koreanText !== undefined ? 'ko' : undefined),
      targetLang: r.targetLang ?? (englishText !== undefined ? 'en' : undefined),
    }))
  } catch {
    return []
  }
}

export function useQuickTranslateHistory() {
  const [history, setHistory] = useState<TranslationRecord[]>([])

  // SSR-safe: load from localStorage after mount
  useEffect(() => {
    setHistory(loadHistory())
  }, [])

  const persist = useCallback((next: TranslationRecord[]) => {
    setHistory(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // quota exceeded — silently ignore
    }
  }, [])

  const addRecord = useCallback(
    (record: Omit<TranslationRecord, 'id' | 'createdAt'>) => {
      const entry: TranslationRecord = {
        ...record,
        id: crypto.randomUUID(),
        createdAt: new Date().toISOString(),
      }
      const next = [entry, ...history].slice(0, MAX_ITEMS)
      persist(next)
    },
    [history, persist]
  )

  const deleteRecord = useCallback(
    (id: string) => {
      persist(history.filter((r) => r.id !== id))
    },
    [history, persist]
  )

  const clearAll = useCallback(() => {
    persist([])
  }, [persist])

  return { history, addRecord, deleteRecord, clearAll }
}
