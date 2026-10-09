'use client'

import { useEffect, useState } from 'react'
import { htmlToMarkdown } from '@/lib/htmlToMarkdown'
import type { MeetingPage } from '@/types/meeting'

export type PageGenStatus = 'idle' | 'generating' | 'error'

export interface PageGenState {
  status: PageGenStatus
  log: string[]
  error?: string
  startedAt?: number
  prevPageId?: string // 생성 시작 시점의 pageId — 재연결 후 새 페이지가 생겼는지 판단한다
}

export const PAGE_GEN_IDLE: PageGenState = { status: 'idle', log: [] }

interface Props {
  page?: MeetingPage
  gen: PageGenState
  hasMessages: boolean
  wsConnected: boolean
  unpublishing?: boolean
  unpublishError?: string
  onGenerate: () => void
  onUnpublish: () => void
  onClose?: () => void
}

function useElapsed(startedAt?: number, active?: boolean) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  if (!startedAt || !active) return ''
  const s = Math.floor((now - startedAt) / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const btn =
  'flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed'
const btnNeutral = `${btn} bg-slate-100 dark:bg-slate-700/50 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600/50`
const btnPrimary = `${btn} bg-cyan-600 text-white hover:bg-cyan-500`

export default function MeetingPagePanel({
  page,
  gen,
  hasMessages,
  wsConnected,
  unpublishing = false,
  unpublishError,
  onGenerate,
  onUnpublish,
  onClose,
}: Props) {
  const [copied, setCopied] = useState(false)
  const [mdState, setMdState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [confirmUnpublish, setConfirmUnpublish] = useState(false)
  const generating = gen.status === 'generating'
  const elapsed = useElapsed(gen.startedAt, generating)

  const handleShare = async () => {
    if (!page) return
    const data = {
      title: page.title ?? '미팅 페이지',
      text: page.shareSummary ?? '',
      url: page.url,
    }
    if (typeof navigator.share === 'function') {
      try {
        await navigator.share(data)
        return
      } catch (err) {
        // 사용자가 공유 시트를 닫은 경우(AbortError)만 그대로 끝낸다 — 그 밖의 실패는 링크 복사로 대신한다
        if ((err as { name?: string } | null)?.name === 'AbortError') return
      }
    }
    try {
      await navigator.clipboard.writeText(page.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy link:', err)
    }
  }

  // 페이지 본문을 마크다운으로 복사한다. iframe 은 sandbox 라 문서를 직접 읽을 수 없고,
  // 페이지는 앱과 같은 오리진(/p/…)에서 서빙되므로 다시 받아서 변환한다.
  const handleCopyMarkdown = async () => {
    if (!page || mdState === 'busy') return
    setMdState('busy')
    try {
      const res = await fetch(page.url, { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const md = htmlToMarkdown(await res.text())
      if (!md.trim()) throw new Error('empty page')
      await navigator.clipboard.writeText(md)
      setMdState('done')
    } catch (err) {
      console.error('Failed to copy page as markdown:', err)
      setMdState('error')
    }
    setTimeout(() => setMdState('idle'), 2000)
  }

  const mdLabel = { idle: '마크다운 복사', busy: '복사 중…', done: '복사됨', error: '복사 실패' }[
    mdState
  ]

  const canGenerate = hasMessages && wsConnected && !generating

  return (
    <div className="flex flex-col h-full w-full glass-sidebar border-l border-slate-200/60 dark:border-cyan-500/10">
      {/* Header */}
      <div className="px-4 h-12 flex items-center justify-between flex-shrink-0 border-b border-slate-200 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            className="w-4 h-4 text-slate-500"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M3 9h18M9 21V9" />
          </svg>
          <span className="text-[11px] font-bold uppercase tracking-widest text-slate-500 dark:text-slate-400">
            미팅 페이지
          </span>
        </div>
        <div className="flex items-center gap-1">
          {page && !generating && (
            <>
              <button onClick={handleShare} title="공유 링크" className={btnPrimary}>
                {copied ? '링크 복사됨' : '공유'}
              </button>
              <button
                onClick={handleCopyMarkdown}
                disabled={mdState === 'busy'}
                title="페이지 내용을 마크다운 텍스트로 복사"
                className={`${btnNeutral} ${mdState === 'error' ? 'text-red-600 dark:text-red-400' : ''}`}
              >
                {mdLabel}
              </button>
              <a
                href={page.url}
                target="_blank"
                rel="noopener noreferrer"
                title="새 탭에서 열기"
                className={btnNeutral}
              >
                새 탭
              </a>
            </>
          )}
          {onClose && (
            <button
              onClick={onClose}
              title="닫기"
              aria-label="닫기"
              className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                className="w-4 h-4"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 flex flex-col">
        {generating && (
          <div className="p-4 flex flex-col gap-3 overflow-y-auto">
            <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <span className="w-2 h-2 rounded-full bg-cyan-500 animate-pulse" />
              페이지를 만들고 있습니다{' '}
              {elapsed && <span className="font-mono text-xs text-slate-400">{elapsed}</span>}
            </div>
            <ol className="text-xs text-slate-500 dark:text-slate-400 space-y-1">
              {gen.log.slice(-12).map((line, i) => (
                <li key={`${i}-${line}`} className="truncate">
                  {line}
                </li>
              ))}
            </ol>
            <p className="text-[11px] text-slate-400">
              보통 2~5분 걸립니다. 창을 닫아도 완료되면 저장됩니다.
            </p>
          </div>
        )}

        {!generating && gen.status === 'error' && (
          <div className="p-4 flex flex-col gap-3">
            <p className="text-sm text-red-600 dark:text-red-400">
              {gen.error ?? '페이지 생성에 실패했습니다.'}
            </p>
            <button onClick={onGenerate} disabled={!canGenerate} className={btnPrimary}>
              다시 시도
            </button>
          </div>
        )}

        {!generating && gen.status !== 'error' && !page && (
          <div className="p-4 flex flex-col gap-3">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              대화 기록으로 공유용 미팅록 페이지를 만듭니다. 결정 사항, 액션 아이템, 논의 흐름, 관련
              공식 자료, 인사이트가 한 페이지에 정리됩니다.
            </p>
            <button onClick={onGenerate} disabled={!canGenerate} className={btnPrimary}>
              미팅 페이지 만들기
            </button>
            {!hasMessages && (
              <p className="text-[11px] text-slate-400">대화 기록이 있어야 만들 수 있습니다.</p>
            )}
            {hasMessages && !wsConnected && (
              <p className="text-[11px] text-slate-400">서버 연결을 기다리고 있습니다.</p>
            )}
          </div>
        )}

        {!generating && gen.status !== 'error' && page && (
          <>
            <iframe
              title="미팅 페이지 미리보기"
              src={page.url}
              sandbox=""
              className="flex-1 w-full bg-white dark:bg-slate-950 border-0"
            />
            {unpublishError && (
              <p className="px-4 pt-2 text-[11px] text-red-600 dark:text-red-400 border-t border-slate-200 dark:border-slate-800">
                {unpublishError}
              </p>
            )}
            <div className="px-4 py-2 flex items-center justify-between gap-2 border-t border-slate-200 dark:border-slate-800 text-[11px] text-slate-400">
              <span className="truncate">
                {new Date(page.generatedAt).toLocaleString('ko-KR')} 생성
              </span>
              <div className="flex items-center gap-1">
                {confirmRegen ? (
                  <>
                    <span className="text-amber-600 dark:text-amber-400">
                      기존 링크는 더 이상 열리지 않습니다.
                    </span>
                    <button
                      onClick={() => {
                        setConfirmRegen(false)
                        onGenerate()
                      }}
                      disabled={!canGenerate}
                      className={btnPrimary}
                    >
                      재생성
                    </button>
                    <button onClick={() => setConfirmRegen(false)} className={btnNeutral}>
                      취소
                    </button>
                  </>
                ) : confirmUnpublish ? (
                  <>
                    <span className="text-amber-600 dark:text-amber-400">링크를 삭제할까요?</span>
                    <button
                      onClick={() => {
                        setConfirmUnpublish(false)
                        onUnpublish()
                      }}
                      disabled={unpublishing}
                      className={`${btn} bg-red-600 text-white hover:bg-red-500`}
                    >
                      공유 해제
                    </button>
                    <button onClick={() => setConfirmUnpublish(false)} className={btnNeutral}>
                      취소
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => setConfirmRegen(true)}
                      disabled={!canGenerate}
                      className={btnNeutral}
                    >
                      재생성
                    </button>
                    <button
                      onClick={() => setConfirmUnpublish(true)}
                      disabled={unpublishing}
                      className={btnNeutral}
                    >
                      {unpublishing ? '해제 중…' : '공유 해제'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
