import { useQuery } from '@tanstack/react-query'
import { getNodes, type Node } from '../lib/api'
import type { Conn } from '../lib/config'
import { Globe, type Marker } from './Globe'
import { shortId } from '../lib/format'

export function GlobePanel({ conn, onClose }: { conn: Conn; onClose: () => void }) {
  const nodesQ = useQuery({
    queryKey: ['nodes', conn.api],
    queryFn: ({ signal }) => getNodes(conn, signal),
    refetchInterval: 4000,
  })
  const nodes = nodesQ.data ?? []
  const located = nodes.filter((n): n is Node & { geo: NonNullable<Node['geo']> } => !!n.geo)
  const markers: Marker[] = located.map((n) => ({
    location: [n.geo.lat, n.geo.lng],
    size: n.ready ? 0.07 : 0.04,
  }))

  return (
    <aside className="flex w-[340px] shrink-0 flex-col border-l border-line/50">
      <div className="flex items-center justify-between px-5 py-3">
        <span className="label">{nodes.length} node{nodes.length === 1 ? '' : 's'}</span>
        <button onClick={onClose} className="font-mono text-[13px] text-dim hover:text-ink">
          ✕
        </button>
      </div>
      <div className="px-5">
        <Globe markers={markers} />
      </div>
      <div className="mt-2 flex-1 space-y-1 overflow-y-auto px-3 pb-4">
        {nodes.map((n) => (
          <div key={n.id} className="flex items-center gap-3 rounded-xl px-2 py-2">
            <span className={`size-1.5 shrink-0 rounded-full ${n.ready ? 'bg-live' : 'bg-signal'}`} />
            <span className="flex-1 truncate font-mono text-[12px] text-ink">
              {n.alias || shortId(n.id)}
            </span>
            <span className="truncate font-mono text-[11px] text-faint">
              {n.geo?.city || n.geo?.country || ''}
            </span>
          </div>
        ))}
      </div>
    </aside>
  )
}
