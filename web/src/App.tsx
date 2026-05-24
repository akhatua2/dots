import { useEffect, useState } from 'react'
import { loadConn, clearConn, type Conn } from './lib/config'
import { Sidebar, type View } from './components/Sidebar'
import { Connect } from './components/Connect'
import { ChatView } from './views/ChatView'
import { DashboardView } from './views/DashboardView'
import { PoolView } from './views/PoolView'

export default function App() {
  const [conn, setConn] = useState<Conn | null>(() => loadConn())
  const [view, setView] = useState<View>('chat')

  // Re-read on hash change (someone pastes a fresh invite link while open).
  useEffect(() => {
    const onHash = () => {
      const c = loadConn()
      if (c) setConn(c)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (!conn) return <Connect onConnect={setConn} />

  const disconnect = () => {
    clearConn()
    setConn(null)
  }

  return (
    <div className="flex h-full">
      <Sidebar view={view} onView={setView} conn={conn} onDisconnect={disconnect} />
      <main className="flex-1 min-w-0">
        {view === 'chat' && <ChatView conn={conn} />}
        {view === 'dashboard' && <DashboardView conn={conn} />}
        {view === 'pool' && <PoolView conn={conn} />}
      </main>
    </div>
  )
}
