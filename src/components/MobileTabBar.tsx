'use client'

import Tabs from '@/components/ui/Tabs'

interface Props {
  activeTab: 'voice' | 'notes' | 'summary' | 'page'
  onTabChange: (tab: 'voice' | 'notes' | 'summary' | 'page') => void
  hasNewVoice?: boolean
  hasNewNotes?: boolean
  hasNewPage?: boolean
}

export default function MobileTabBar({
  activeTab,
  onTabChange,
  hasNewVoice,
  hasNewNotes,
  hasNewPage,
}: Props) {
  return (
    <div className="shrink-0 bg-panel px-2 lg:hidden">
      <Tabs
        label="회의 화면"
        value={activeTab}
        onChange={onTabChange}
        items={[
          {
            value: 'voice',
            label: '대화',
            icon: 'waveform',
            badge: hasNewVoice,
            panelId: 'meeting-voice-panel',
          },
          {
            value: 'notes',
            label: '내 메모',
            icon: 'notes',
            badge: hasNewNotes,
            panelId: 'meeting-utility-panel',
          },
          { value: 'summary', label: '요약', icon: 'summary', panelId: 'meeting-utility-panel' },
          {
            value: 'page',
            label: '회의 페이지',
            icon: 'page',
            badge: hasNewPage,
            panelId: 'meeting-utility-panel',
          },
        ]}
        className="[&_.ui-tab]:min-h-11 [&_.ui-tab]:gap-1 [&_.ui-tab]:px-1.5 [&_.ui-tab_svg]:hidden min-[380px]:[&_.ui-tab_svg]:block"
      />
    </div>
  )
}
