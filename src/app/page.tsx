import Header from '@/components/Header'
import SubtitleArea from '@/components/SubtitleArea'
import ControlPanel from '@/components/ControlPanel'

export default function Home() {
  return (
    <main className="relative flex flex-col h-screen overflow-hidden font-sans">
      {/* Gradient mesh background */}
      <div className="absolute inset-0 -z-10 bg-gradient-to-br from-slate-50 via-indigo-50/60 to-violet-50/80 dark:from-[#070614] dark:via-[#0b0820] dark:to-[#0f0828]" />

      {/* Ambient orbs */}
      <div className="orb-a absolute top-[8%] left-[3%] w-72 h-72 rounded-full bg-indigo-300/20 dark:bg-indigo-600/18 blur-3xl pointer-events-none -z-10" />
      <div className="orb-b absolute top-[35%] right-[5%] w-96 h-96 rounded-full bg-violet-300/15 dark:bg-violet-700/14 blur-3xl pointer-events-none -z-10" />
      <div className="orb-c absolute bottom-[15%] left-[25%] w-56 h-56 rounded-full bg-cyan-300/15 dark:bg-cyan-600/10 blur-3xl pointer-events-none -z-10" />

      <Header />
      <div className="flex-1 overflow-hidden">
        <SubtitleArea />
      </div>
      <ControlPanel />
    </main>
  )
}
