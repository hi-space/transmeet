import Header from '@/components/Header'
import SubtitleArea from '@/components/SubtitleArea'
import ControlPanel from '@/components/ControlPanel'

export default function Home() {
  return (
    <main className="flex flex-col h-screen bg-slate-50 dark:bg-slate-900">
      <Header />
      <div className="flex-1 overflow-hidden">
        <SubtitleArea />
      </div>
      <ControlPanel />
    </main>
  )
}
