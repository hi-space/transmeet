'use client'

import { useState, useCallback, useEffect } from 'react'

export interface TranslationRecord {
  id: string
  koreanText: string
  englishText: string
  audioData?: string
  createdAt: string
}

const STORAGE_KEY = 'transmeet-quick-translate-history'
const MAX_ITEMS = 20

function loadHistory(): TranslationRecord[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as TranslationRecord[]) : []
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
