import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import EmailNotifySetting from '@/components/EmailNotifySetting'
import { useSettings } from '@/hooks/useSettings'
import { useWebSocket } from '@/hooks/useWebSocket'

function Fixture() {
  const { settings, updateSettings } = useSettings()
  const [visible, setVisible] = useState(true)
  const { connect, disconnect, sendEndMeeting } = useWebSocket({
    meetingId: 'fixture-meeting',
    onMessage: () => {},
  })
  useEffect(() => {
    connect()
    return disconnect
  }, [connect, disconnect])
  return (
    <main className="w-full space-y-5 p-5" style={{ maxWidth: 448, margin: '32px auto' }}>
      <h1 className="text-lg font-semibold text-ink">회의 메일 설정</h1>
      {visible && (
        <div className="ui-surface p-5">
          <EmailNotifySetting settings={settings} onUpdate={updateSettings} />
        </div>
      )}
      <button className="ui-button" onClick={() => setVisible((value) => !value)}>
        설정 {visible ? '닫기' : '열기'}
      </button>
      <button
        className="ui-button"
        onClick={() => sendEndMeeting('fixture-meeting', settings.emailOnEnd)}
      >
        회의 종료
      </button>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<Fixture />)
