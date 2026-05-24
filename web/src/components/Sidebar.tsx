import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../lib/api'
import type { Conn } from '../lib/config'

export type View = 'chat' | 'dashboard' | 'pool'

const NAV: { id: View; label: string; icon: string }[] = [
  { id: 'chat', label: 'Chat', icon: '💬' },
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
  { id: 'pool', label: 'Pool', icon: '🌐' },
]

export function Sidebar({
  view,
  onView,
  conn,
  onDisconnect,
}: {
  view: View
  onView: (v: View) => void
  conn: Conn
  onDisconnect: () => void
}) {
  const health = useQuery({
    queryKey: ['health', conn.api],
    queryFn: ({ signal }) => getHealth(conn, signal),
    refetchInterval: 5000,
  })

  const ready = health.data?.ready ?? false
  const workers = health.data?.workers ?? 0
  const dotColor = health.isError
    ? 'bg-bad'
    : ready
      ? 'bg-good'
      : 'bg-amber-400'
  const statusText = health.isError
    ? 'Offline'
    : ready
      ? `${workers} worker${workers === 1 ? '' : 's'} online`
      : 'Pool warming up…'

  return (
    <aside className="w-60 shrink-0 border-r border-border bg-panel flex flex-col">
      <div className="px-5 py-5 flex items-center gap-2">
        <div className="size-7 rounded-lg bg-gradient-to-br from-accent to-accent-2" />
        <span className="text-lg font-semibold tracking-tight">dots</span>
      </div>

      <nav className="px-3 flex flex-col gap-1">
        {NAV.map((n) => (
          <button
            key={n.id}
            onClick={() => onView(n.id)}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
              view === n.id
                ? 'bg-panel-2 text-text'
                : 'text-muted hover:text-text hover:bg-panel-2/50'
            }`}
          >
            <span className="text-base">{n.icon}</span>
            {n.label}
          </button>
        ))}
      </nav>

      <div className="mt-auto px-3 pb-4 space-y-3">
        <a
          href="#contribute"
          className="block rounded-lg bg-gradient-to-br from-accent to-accent-2 px-3 py-2.5 text-center text-sm font-medium text-white shadow-lg shadow-accent/20 hover:opacity-95"
        >
          Contribute your Mac
        </a>

        <div className="rounded-lg bg-panel-2 px-3 py-2.5">
          <div className="flex items-center gap-2 text-xs">
            <span className={`size-2 rounded-full ${dotColor}`} />
            <span className="text-muted">{statusText}</span>
          </div>
          <div className="mt-1 truncate text-[11px] text-muted/70" title={conn.api}>
            {conn.api.replace(/^https?:\/\//, '')}
          </div>
        </div>

        <button
          onClick={onDisconnect}
          className="w-full text-left text-xs text-muted hover:text-text px-1"
        >
          Disconnect
        </button>
      </div>
    </aside>
  )
}
