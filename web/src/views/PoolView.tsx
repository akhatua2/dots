import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../lib/api'
import type { Conn } from '../lib/config'

export function PoolView({ conn }: { conn: Conn }) {
  const health = useQuery({
    queryKey: ['health', conn.api],
    queryFn: ({ signal }) => getHealth(conn, signal),
    refetchInterval: 3000,
  })

  const ready = health.data?.ready ?? false
  const workers = health.data?.workers ?? 0
  const modelList = health.data?.models ?? []

  return (
    <div className="h-full overflow-y-auto">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-sm font-medium">Pool</h1>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-6">
        <div className="rounded-xl border border-border bg-panel p-6">
          <div className="flex items-center gap-3">
            <span
              className={`size-3 rounded-full ${
                health.isError ? 'bg-bad' : ready ? 'bg-good' : 'bg-amber-400'
              }`}
            />
            <span className="text-lg font-medium">
              {health.isError ? 'Pool unreachable' : ready ? 'Pool online' : 'Warming up'}
            </span>
          </div>
          <p className="mt-2 text-sm text-muted">
            {ready
              ? `${workers} Mac${workers === 1 ? '' : 's'} are contributing compute right now.`
              : 'Waiting for compute workers to come online.'}
          </p>
        </div>

        <h2 className="mt-8 mb-3 text-xs font-medium uppercase tracking-wide text-muted">
          Models served
        </h2>
        {modelList.length === 0 ? (
          <p className="text-sm text-muted">No models deployed yet.</p>
        ) : (
          <ul className="space-y-2">
            {modelList.map((m) => (
              <li
                key={m}
                className="flex items-center justify-between rounded-lg border border-border bg-panel px-4 py-3 font-mono text-sm"
              >
                <span>{m}</span>
                <span className="text-xs text-good">deployed</span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-10 rounded-xl border border-accent/30 bg-accent/5 p-6">
          <h3 className="text-base font-medium">Want to make the pool faster?</h3>
          <p className="mt-1 text-sm text-muted">
            Contribute your Mac's compute and earn CU. Setup is one click — download,
            open, done.
          </p>
          <a
            href="#contribute"
            className="mt-4 inline-block rounded-lg bg-gradient-to-br from-accent to-accent-2 px-4 py-2 text-sm font-medium text-white"
          >
            Contribute your Mac
          </a>
        </div>
      </div>
    </div>
  )
}
