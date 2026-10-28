'use client'

import { useEffect, useState, type Ref } from 'react'
import { htmlToMarkdown } from '@/lib/htmlToMarkdown'
import { copyText, type ClipboardContent } from '@/lib/clipboard'
import { createPageClipboardContent } from '@/lib/clipboardContent'
import type { MeetingPage } from '@/types/meeting'
import Icon from '@/components/ui/Icon'
import { DEFAULT_PAGE_STYLE, PAGE_STYLES, type PageStyle } from '@/lib/pageStyles'
import MarkdownCopyButtons from '@/components/MarkdownCopyButtons'

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
  onGenerate: (style: PageStyle) => void
  onUnpublish: () => void
  onClose?: () => void
  footerRef?: Ref<HTMLDivElement>
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

// 페이지 URL 의 호스트(transmeet.hi-yoo.com)가 앱을 연 호스트와 다르면 — CloudFront 기본 도메인이나
// localhost — 교차 오리진이라 fetch 가 CORS 로 막힌다. 같은 배포가 /p/* 도 서빙하므로 현재 오리진의
// 같은 경로를 먼저 시도하고, 그래도 안 되면 원래 URL 을 시도한다.
function pageFetchUrls(url: string): string[] {
  try {
    const u = new URL(url)
    const same = `${window.location.origin}${u.pathname}${u.search}`
    return same === url ? [url] : [same, url]
  } catch {
    return [url]
  }
}

async function fetchPageHtml(url: string, signal: AbortSignal): Promise<string> {
  let lastErr: unknown
  for (const candidate of pageFetchUrls(url)) {
    try {
      const res = await fetch(candidate, { cache: 'no-store', signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return await res.text()
    } catch (err) {
      if (signal.aborted) throw err
      lastErr = err
    }
  }
  throw lastErr
}

const btn = 'ui-button'
const btnNeutral = 'ui-button ui-button-secondary'
const btnPrimary = 'ui-button ui-button-primary'

// 만들 페이지 모양 고르기 — 설명이 있는 라디오 목록 (선택지 셋이라 드롭다운보다 한눈에 보인다)
function PageStylePicker({
  value,
  onChange,
  disabled,
}: {
  value: PageStyle
  onChange: (style: PageStyle) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="w-full max-w-xs space-y-1.5 text-left" disabled={disabled}>
      <legend className="mb-1.5 text-xs font-medium text-ink">페이지 모양</legend>
      {PAGE_STYLES.map((option) => (
        <label
          key={option.value}
          className={`flex cursor-pointer gap-2 rounded-lg border p-2.5 text-xs transition-colors ${
            value === option.value
              ? 'border-accent-600 bg-accent-50 dark:border-accent-400 dark:bg-accent-500/10'
              : 'border-subtle hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'
          }`}
        >
          <input
            type="radio"
            name="page-style"
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            className="mt-0.5 accent-accent-600"
          />
          <span>
            <span className="block font-medium text-ink">{option.label}</span>
            <span className="mt-0.5 block leading-relaxed text-muted">{option.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

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
  footerRef,
}: Props) {
  const [copied, setCopied] = useState(false)
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [style, setStyle] = useState<PageStyle>(DEFAULT_PAGE_STYLE)
  const [confirmUnpublish, setConfirmUnpublish] = useState(false)
  const generating = gen.status === 'generating'
  const elapsed = useElapsed(gen.startedAt, generating)

  const handleShare = async () => {
    if (!page) return
    const data = {
      title: page.title ?? '회의 페이지',
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
      await copyText(page.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy link:', err)
    }
  }

  // 페이지 본문의 마크다운은 페이지가 보일 때 미리 만들어 둔다. iframe 은 sandbox 라 문서를 직접
  // 읽을 수 없어 같은 오리진(/p/…) URL 을 다시 받아 변환한다. 클릭 시점에 fetch 를 await 하고
  // 나서 클립보드에 쓰면 Safari 가 사용자 제스처 밖이라고 거부하므로, 클릭 핸들러는 await 없이
  // 준비된 내용만 바로 쓴다. 서식 복사는 원본 HTML 을 사용해 기호·코드·줄바꿈을 보존한다.
  const [markdown, setMarkdown] = useState<{
    url: string
    text?: string
    content?: ClipboardContent
    error?: string
  } | null>(null)
  const [mdRetry, setMdRetry] = useState(0)
  const pageUrl = page?.url
  useEffect(() => {
    if (!pageUrl) {
      setMarkdown(null)
      return
    }
    const ctrl = new AbortController()
    setMarkdown({ url: pageUrl })
    fetchPageHtml(pageUrl, ctrl.signal)
      .then((html) => {
        if (ctrl.signal.aborted) return
        const text = htmlToMarkdown(html)
        if (!text.trim()) throw new Error('페이지 내용이 비어 있습니다')
        setMarkdown({ url: pageUrl, text, content: createPageClipboardContent(html, pageUrl) })
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return
        console.error('Failed to load page as markdown:', err)
        setMarkdown({ url: pageUrl, error: err instanceof Error ? err.message : String(err) })
      })
    return () => ctrl.abort()
  }, [pageUrl, mdRetry])

  const currentMarkdown = markdown?.url === pageUrl ? markdown : null
  const mdLoading = !currentMarkdown?.text && !currentMarkdown?.error

  const canGenerate = hasMessages && wsConnected && !generating

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-panel">
      {(page || onClose) && (
        <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-1 border-b border-subtle px-2.5 py-1.5">
          {page && !generating && (
            <>
              <button
                type="button"
                onClick={handleShare}
                title="회의 페이지 공유"
                className="ui-button ui-button-tinted gap-1.5 px-2"
              >
                <Icon name={copied ? 'check' : 'send'} className="h-3.5 w-3.5" />
                {copied ? '링크 복사됨' : '공유'}
              </button>
              {currentMarkdown?.text ? (
                <MarkdownCopyButtons
                  key={pageUrl}
                  markdown={currentMarkdown.text}
                  content={currentMarkdown.content}
                  compact
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setMdRetry((n) => n + 1)}
                  disabled={mdLoading}
                  aria-busy={mdLoading}
                  title={
                    currentMarkdown?.error
                      ? `페이지를 불러오지 못했습니다: ${currentMarkdown.error}`
                      : '복사할 내용 준비 중'
                  }
                  className={`ui-button gap-1.5 px-2 ${currentMarkdown?.error ? 'text-red-600 dark:text-red-400' : ''}`}
                >
                  <Icon name="copy" className="h-3.5 w-3.5" />
                  {currentMarkdown?.error ? '불러오기 실패 · 재시도' : '준비 중…'}
                </button>
              )}
              <a
                href={page.url}
                target="_blank"
                rel="noopener noreferrer"
                title="새 탭에서 열기"
                className="ui-button ml-auto px-2"
              >
                새 탭<Icon name="arrowRight" className="h-3 w-3 -rotate-45" />
              </a>
            </>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="회의 페이지 닫기"
              className="ui-icon-button"
            >
              <Icon name="close" />
            </button>
          )}
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col">
        {generating && (
          <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            <div role="status" className="flex items-center gap-2 text-sm text-ink">
              <Icon
                name="loader"
                className="h-4 w-4 animate-spin text-accent-600 dark:text-accent-400"
              />
              페이지 생성 중
              {elapsed && (
                <span className="ml-auto text-xs tabular-nums text-muted">{elapsed}</span>
              )}
            </div>
            <ol className="space-y-2 border-l border-subtle pl-3 text-xs leading-relaxed text-muted">
              {gen.log.slice(-12).map((line, index) => (
                <li key={`${index}-${line}`} className="break-words">
                  {line}
                </li>
              ))}
            </ol>
            <p className="text-xs text-muted">창을 닫아도 생성은 계속됩니다.</p>
          </div>
        )}
        {!generating && gen.status === 'error' && (
          <div className="ui-empty">
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {gen.error ?? '페이지 생성에 실패했습니다.'}
            </p>
            <button
              type="button"
              onClick={() => onGenerate(style)}
              disabled={!canGenerate}
              className={`${btnPrimary} mt-2`}
            >
              <Icon name="refresh" />
              다시 시도
            </button>
          </div>
        )}
        {!generating && gen.status !== 'error' && !page && (
          <div className="ui-empty">
            <div className="ui-empty-icon">
              <Icon name="page" className="h-6 w-6" />
            </div>
            <p className="text-sm font-medium text-ink">공유할 회의 페이지</p>
            <p className="text-xs">
              {!hasMessages
                ? '대화가 쌓이면 만들 수 있습니다'
                : !wsConnected
                  ? '서버 연결을 기다리고 있습니다'
                  : '대화와 결정 사항을 한 페이지로 정리합니다'}
            </p>
            {hasMessages && (
              <div className="mt-3 flex w-full justify-center">
                <PageStylePicker value={style} onChange={setStyle} disabled={!canGenerate} />
              </div>
            )}
            <button
              type="button"
              onClick={() => onGenerate(style)}
              disabled={!canGenerate}
              className={`${btnPrimary} mt-3`}
            >
              <Icon name="plus" />
              페이지 만들기
            </button>
          </div>
        )}
        {!generating && gen.status !== 'error' && page && (
          <>
            <iframe
              title="회의 페이지 미리보기"
              src={page.url}
              sandbox=""
              className="min-h-0 w-full flex-1 border-0 bg-white"
            />
            <div
              ref={footerRef}
              role="group"
              aria-label="회의 페이지 작업"
              className="shrink-0 border-t border-subtle p-3"
            >
              {unpublishError && (
                <p role="alert" className="mb-2 text-xs text-red-600 dark:text-red-400">
                  {unpublishError}
                </p>
              )}
              {confirmRegen || confirmUnpublish ? (
                <div className="space-y-2">
                  {confirmRegen && <PageStylePicker value={style} onChange={setStyle} />}
                  <p className="text-xs leading-relaxed text-amber-700 dark:text-amber-300">
                    {confirmRegen
                      ? '다시 만들면 기존 공유 링크는 만료됩니다.'
                      : '이 페이지의 공유 링크를 삭제할까요?'}
                  </p>
                  <div className="flex justify-end gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setConfirmRegen(false)
                        setConfirmUnpublish(false)
                      }}
                      className={btnNeutral}
                    >
                      취소
                    </button>
                    {confirmRegen ? (
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmRegen(false)
                          onGenerate(style)
                        }}
                        disabled={!canGenerate}
                        className={btnPrimary}
                      >
                        다시 만들기
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setConfirmUnpublish(false)
                          onUnpublish()
                        }}
                        disabled={unpublishing}
                        className={`${btn} bg-red-600 text-white hover:!bg-red-700 hover:!text-white`}
                      >
                        공유 해제
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[0.6875rem] text-muted">
                    {new Date(page.generatedAt).toLocaleDateString('ko-KR')} 생성
                  </span>
                  <div className="ml-auto flex gap-1">
                    <button
                      type="button"
                      onClick={() => setConfirmRegen(true)}
                      disabled={!canGenerate}
                      className="ui-button"
                    >
                      다시 만들기
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmUnpublish(true)}
                      disabled={unpublishing}
                      className="ui-button"
                    >
                      {unpublishing ? '해제 중…' : '공유 해제'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
