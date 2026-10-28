'use client'

import { useEffect, useRef, useState } from 'react'
import type { Settings } from '@/hooks/useSettings'
import { supportsSystemAudio } from '@/lib/platform'
import ThemeSettings from '@/components/ThemeSettings'
import EmailNotifySetting from '@/components/EmailNotifySetting'
import SummaryTemplateSettings from '@/components/SummaryTemplateSettings'
import type { SummaryTemplateInput } from '@/lib/api'
import type { SummaryTemplate } from '@/lib/summaryStyles'
import { TRANSLATION_MODELS } from '@/lib/translationModels'
import {
  STT_CHUNK_SECONDS,
  STT_PROVIDERS,
  isManagedStt,
  isStreamingStt,
  sttChunkDuration,
  type SttProvider,
  type SttStatus,
} from '@/lib/sttProviders'
import SttStatusPanel from '@/components/SttStatusPanel'
import Dialog from '@/components/ui/Dialog'
import Icon from '@/components/ui/Icon'
import Tabs from '@/components/ui/Tabs'
import Switch from '@/components/ui/Switch'

interface Props {
  settings: Settings
  onUpdate: (patch: Partial<Settings>) => void
  onClose: () => void
  sttStatus?: SttStatus
  sttConnected?: boolean
  onSttSelect?: (provider: SttProvider) => void
  onPrepareStt?: () => void
  initialTab?: SettingsTab
  // 내 양식 — 주지 않으면(로컬 전용 모드) 요약 양식 탭을 숨긴다
  summaryTemplates?: {
    templates: SummaryTemplate[]
    error?: string
    save: (input: SummaryTemplateInput, templateId?: string) => Promise<unknown>
    remove: (templateId: string) => Promise<unknown>
  }
}

export type SettingsTab = 'general' | 'theme' | 'summary'

const TAB_LABELS: Record<SettingsTab, string> = {
  general: '음성 · 번역',
  theme: '테마',
  summary: '요약 양식',
}

const VOICES: Record<'en' | 'ko', { id: string; label: string; engines: string[] }[]> = {
  en: [
    { id: 'Joanna', label: 'Joanna', engines: ['generative', 'neural', 'standard'] },
    { id: 'Ruth', label: 'Ruth', engines: ['generative', 'neural'] },
    { id: 'Tiffany', label: 'Tiffany', engines: ['generative'] },
  ],
  ko: [{ id: 'Seoyeon', label: 'Seoyeon', engines: ['neural'] }],
}

function Choices<T extends string | number>({
  label,
  value,
  options,
  onChange,
  hint,
  columns,
}: {
  label: string
  value: T
  options: readonly { value: T; label: string; disabled?: boolean }[]
  onChange: (value: T) => void
  hint?: string
  columns?: number
}) {
  return (
    <fieldset className="min-w-0 space-y-2">
      <legend className="ui-setting-label mb-2">{label}</legend>
      <div
        className="grid gap-1.5"
        style={{ gridTemplateColumns: `repeat(${columns ?? options.length}, minmax(0, 1fr))` }}
      >
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            disabled={option.disabled}
            aria-pressed={value === option.value}
            className={`ui-button min-w-0 px-1.5 ${value === option.value ? 'ui-button-tinted border-accent-200 dark:border-accent-700' : 'ui-button-secondary'}`}
          >
            {option.label}
          </button>
        ))}
      </div>
      {hint && <p className="ui-setting-hint">{hint}</p>}
    </fieldset>
  )
}

export default function SettingsPanel({
  settings,
  onUpdate,
  onClose,
  sttStatus,
  sttConnected = false,
  onSttSelect,
  onPrepareStt,
  initialTab = 'general',
  summaryTemplates,
}: Props) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(
    initialTab === 'summary' && !summaryTemplates ? 'general' : initialTab
  )
  const scrollRef = useRef<HTMLDivElement>(null)
  const [canUseSystemAudio, setCanUseSystemAudio] = useState(true)
  const streamingStt = isStreamingStt(settings.sttProvider, sttStatus)
  const modelStreaming = settings.sttProvider !== 'transcribe' && streamingStt

  useEffect(() => {
    setCanUseSystemAudio(supportsSystemAudio())
  }, [])
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [activeTab])
  useEffect(() => {
    if (!canUseSystemAudio && settings.audioSource !== 'mic') onUpdate({ audioSource: 'mic' })
  }, [canUseSystemAudio, settings.audioSource, onUpdate])

  const handleUpdate = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch }
    const language = next.sourceLang === 'auto' ? 'en' : next.sourceLang
    if (patch.pollyEngine && patch.pollyEngine !== settings.pollyEngine) {
      const current = VOICES[language].find((voice) => voice.id === next.pollyVoiceId)
      if (!current?.engines.includes(patch.pollyEngine)) {
        const fallback = VOICES[language].find((voice) =>
          voice.engines.includes(patch.pollyEngine!)
        )
        if (fallback) next.pollyVoiceId = fallback.id
      }
    }
    onUpdate(next)
  }

  const ttsLang = settings.sourceLang === 'auto' ? 'en' : settings.sourceLang
  const availableVoices = VOICES[ttsLang].filter((voice) =>
    voice.engines.includes(settings.pollyEngine)
  )

  return (
    <Dialog labelledBy="settings-title" onClose={onClose}>
      <div className="flex shrink-0 items-center justify-between gap-3 px-5 py-3">
        <div className="flex items-center gap-2.5">
          <Icon name="settings" className="h-[18px] w-[18px] text-muted" />
          <h2 id="settings-title" className="text-base font-semibold">
            설정
          </h2>
          <span className="hidden text-[0.6875rem] text-muted min-[360px]:inline">
            변경 즉시 적용
          </span>
        </div>
        <button
          type="button"
          autoFocus
          onClick={onClose}
          aria-label="설정 닫기"
          className="ui-icon-button"
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="shrink-0 px-5 pb-3">
        <Tabs
          label="설정 분류"
          value={activeTab}
          onChange={setActiveTab}
          panelId="settings-content"
          items={[
            { value: 'general', label: TAB_LABELS.general, icon: 'waveform' },
            { value: 'theme', label: TAB_LABELS.theme, icon: 'sun' },
            ...(summaryTemplates
              ? [{ value: 'summary' as const, label: TAB_LABELS.summary, icon: 'summary' as const }]
              : []),
          ]}
        />
      </div>
      <div
        ref={scrollRef}
        id="settings-content"
        role="tabpanel"
        aria-label={TAB_LABELS[activeTab]}
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain border-t border-subtle px-5 py-5"
        style={{ paddingBottom: 'max(20px, env(safe-area-inset-bottom))' }}
      >
        {activeTab === 'summary' && summaryTemplates ? (
          <SummaryTemplateSettings
            templates={summaryTemplates.templates}
            error={summaryTemplates.error}
            onSave={summaryTemplates.save}
            onRemove={summaryTemplates.remove}
          />
        ) : activeTab === 'theme' ? (
          <ThemeSettings
            voiceFontSize={settings.voiceFontSize}
            onVoiceFontSizeChange={(voiceFontSize) => onUpdate({ voiceFontSize })}
          />
        ) : (
          <div className="space-y-5">
            <Choices
              label="오디오 입력"
              value={settings.audioSource}
              options={[
                { value: 'mic', label: '마이크' },
                { value: 'system', label: '시스템 오디오', disabled: !canUseSystemAudio },
                { value: 'both', label: '둘 다', disabled: !canUseSystemAudio },
              ]}
              onChange={(audioSource) => handleUpdate({ audioSource })}
              hint={
                !canUseSystemAudio
                  ? '이 브라우저에서는 마이크 입력만 지원합니다.'
                  : settings.audioSource !== 'mic'
                    ? '화면 공유 시 오디오 공유를 함께 선택하세요.'
                    : undefined
              }
            />
            <div className="border-t border-subtle pt-5">
              <Choices
                label="번역 모델"
                value={settings.translationModel}
                options={TRANSLATION_MODELS.map(({ id, label }) => ({ value: id, label }))}
                onChange={(translationModel) => handleUpdate({ translationModel })}
              />
            </div>
            <Choices
              label="번역 시점"
              value={settings.translationTiming}
              options={[
                { value: 'sentence', label: '문장 완료 후' },
                { value: 'realtime', label: '실시간' },
                { value: 'manual', label: '직접 번역' },
              ]}
              onChange={(translationTiming) => handleUpdate({ translationTiming })}
              hint={
                settings.translationTiming === 'manual'
                  ? '각 대화의 번역 버튼을 눌러 번역합니다.'
                  : undefined
              }
            />
            <div className="space-y-2 border-t border-subtle pt-3">
              <div className="flex items-center justify-between gap-3">
                <span className="ui-setting-label">번역 음성 자동 재생</span>
                <Switch
                  label="번역 음성 자동 재생"
                  checked={settings.ttsAutoPlay}
                  onChange={(ttsAutoPlay) => handleUpdate({ ttsAutoPlay })}
                />
              </div>
              <label className="flex items-center justify-between gap-3 text-xs text-muted">
                목소리
                <select
                  aria-label="목소리"
                  value={settings.pollyVoiceId}
                  onChange={(event) => handleUpdate({ pollyVoiceId: event.target.value })}
                  className="ui-input w-40 py-2 text-xs"
                >
                  {availableVoices.map((voice) => (
                    <option key={voice.id} value={voice.id}>
                      {voice.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="border-t border-subtle pt-5">
              <Choices
                label="자동 요약 주기"
                value={settings.autoSummarizeMessageCount}
                options={[
                  { value: 0, label: '끄기' },
                  { value: 5, label: '5개' },
                  { value: 10, label: '10개' },
                  { value: 20, label: '20개' },
                ]}
                onChange={(autoSummarizeMessageCount) =>
                  handleUpdate({ autoSummarizeMessageCount })
                }
              />
              <div className="mt-3">
                <EmailNotifySetting settings={settings} onUpdate={handleUpdate} />
              </div>
            </div>
            <details className="group border-t border-subtle pt-1">
              <summary className="flex min-h-11 list-none items-center justify-between text-xs font-semibold text-muted [&::-webkit-details-marker]:hidden">
                고급 설정
                <Icon
                  name="chevronDown"
                  className="h-4 w-4 transition-transform group-open:rotate-180"
                />
              </summary>
              <div className="space-y-5 pb-1 pt-3">
                <Choices
                  label="음성 인식 엔진"
                  value={settings.sttProvider}
                  options={STT_PROVIDERS.map(({ id, label }) => ({ value: id, label }))}
                  columns={2}
                  onChange={(sttProvider) =>
                    onSttSelect ? onSttSelect(sttProvider) : handleUpdate({ sttProvider })
                  }
                  hint={
                    settings.sttProvider === 'transcribe'
                      ? '발화 중에도 인식 결과를 표시합니다.'
                      : modelStreaming
                        ? '실시간으로 원문을 표시합니다. 번역은 확정된 발화를 기준으로 합니다.'
                        : settings.sttProvider === 'qwen3-asr'
                          ? '입력 언어는 자동으로 감지합니다. 번역 대상은 상단에서 따로 선택하세요.'
                          : `음성을 약 ${sttChunkDuration(settings.sttProvider, settings.sttChunkSeconds, sttStatus) / 1000}초씩 모아 인식하므로 첫 결과까지 시간이 걸릴 수 있습니다.`
                  }
                />
                {isManagedStt(settings.sttProvider, sttStatus) && (
                  <SttStatusPanel
                    state={sttStatus}
                    connected={sttConnected}
                    onPrepare={onPrepareStt}
                  />
                )}
                {!streamingStt && (
                  <Choices
                    label="음성 처리 구간"
                    value={settings.sttChunkSeconds}
                    options={STT_CHUNK_SECONDS.map((seconds) => ({
                      value: seconds,
                      label: `${seconds}초`,
                    }))}
                    onChange={(sttChunkSeconds) => handleUpdate({ sttChunkSeconds })}
                    hint="기본 6초. 짧게 잡으면 반응이 빨라지고, 길게 잡으면 문맥을 더 확보할 수 있습니다."
                  />
                )}
                {!modelStreaming && (
                  <>
                    <Choices
                      label="발화 중 번역"
                      value={settings.partialTranslationMode}
                      options={[
                        { value: 'sentence', label: '문장 단위' },
                        { value: 'realtime', label: '일정 간격' },
                      ]}
                      onChange={(partialTranslationMode) =>
                        handleUpdate({ partialTranslationMode })
                      }
                    />
                    {settings.partialTranslationMode === 'realtime' && (
                      <Choices
                        label="번역 간격"
                        value={settings.partialThrottleMs}
                        options={[
                          { value: 500, label: '0.5초' },
                          { value: 1000, label: '1초' },
                          { value: 1500, label: '1.5초' },
                          { value: 2000, label: '2초' },
                          { value: 3000, label: '3초' },
                        ]}
                        onChange={(partialThrottleMs) => handleUpdate({ partialThrottleMs })}
                      />
                    )}
                  </>
                )}
                <Choices
                  label="무음 후 대화 나누기"
                  value={settings.silenceTimeout}
                  options={[
                    { value: 3000, label: '3초' },
                    { value: 5000, label: '5초' },
                    { value: 10000, label: '10초' },
                    { value: 20000, label: '20초' },
                  ]}
                  onChange={(silenceTimeout) => handleUpdate({ silenceTimeout })}
                />
                <Choices
                  label="말풍선당 최대 문장"
                  value={settings.maxSentencesPerBubble}
                  options={[
                    { value: 1, label: '1문장' },
                    { value: 2, label: '2문장' },
                    { value: 3, label: '3문장' },
                    { value: 5, label: '5문장' },
                  ]}
                  onChange={(maxSentencesPerBubble) => handleUpdate({ maxSentencesPerBubble })}
                />
              </div>
            </details>
          </div>
        )}
      </div>
    </Dialog>
  )
}
