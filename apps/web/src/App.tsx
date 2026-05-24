import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { loadConn, type Conn } from './lib/config'
import { getCredits } from './lib/api'
import { NoInvite } from './components/NoInvite'
import { ContributePanel } from './components/ContributePanel'
import { GlobePanel } from './components/GlobePanel'
import { DotMark } from './components/DotMark'
import { ChatView } from './views/ChatView'
import { cu } from './lib/format'

type Panel = 'globe' | 'contribute' | null

export default function App() {
  const [conn, setConn] = useState<Conn | null>(() => loadConn())
  const [panel, setPanel] = useState<Panel>(null)

  useEffect(() => {
    const onHash = () => {
      const c = loadConn()
      if (c) setConn(c)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (!conn) return <NoInvite />

  const toggle = (p: Panel) => setPanel((cur) => (cur === p ? null : p))

  return (
    <div className="h-full">
      <div className="flex h-full">
        {/* slim rail: logo + profile, nothing clickable */}
        <div className="flex w-14 shrink-0 flex-col items-center justify-between py-4">
          <DotMark size={20} />
          <div className="size-8 rounded-full bg-gradient-to-br from-signal/80 to-signal-dim" />
        </div>

        {/* the single vertical line */}
        <div className="w-px shrink-0 bg-line" />

        <div className="flex min-h-0 flex-1 flex-col">
          <TopBar
            conn={conn}
            panel={panel}
            onGlobe={() => toggle('globe')}
            onContribute={() => toggle('contribute')}
          />
          <div className="flex min-h-0 flex-1">
            <ChatView conn={conn} />
            {panel === 'globe' && <GlobePanel conn={conn} onClose={() => setPanel(null)} />}
            {panel === 'contribute' && (
              <ContributePanel conn={conn} onClose={() => setPanel(null)} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TopBar({
  conn,
  panel,
  onGlobe,
  onContribute,
}: {
  conn: Conn
  panel: Panel
  onGlobe: () => void
  onContribute: () => void
}) {
  const credits = useQuery({
    queryKey: ['credits', conn.api],
    queryFn: ({ signal }) => getCredits(conn, signal),
    refetchInterval: 5000,
  })

  const pooled = (credits.data?.turns ?? []).reduce((s, t) => s + (t.spent_cu ?? 0), 0)

  return (
    <header className="flex items-center justify-end gap-4 px-5 py-3">
      <div className="flex items-center gap-4">
        <span className="font-mono text-[13px] tabular-nums text-ink">{cu(pooled)} CU</span>
        <button
          onClick={onGlobe}
          className={`rounded-full px-3 py-1.5 font-mono text-[12px] transition-colors ${
            panel === 'globe' ? 'bg-signal/15 text-signal' : 'text-dim hover:bg-surface-2 hover:text-ink'
          }`}
        >
          ◍ Map
        </button>
        <button
          onClick={onContribute}
          className={`rounded-full px-3.5 py-1.5 font-mono text-[12px] transition-opacity ${
            panel === 'contribute' ? 'bg-signal/15 text-signal' : 'bg-signal text-bg hover:opacity-90'
          }`}
        >
          Contribute
        </button>
      </div>
    </header>
  )
}
