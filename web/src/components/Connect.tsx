import { useState } from 'react'
import { saveConn, type Conn } from '../lib/config'
import { getHealth, ApiError } from '../lib/api'

// Shown when there's no saved connection and no invite link was used.
// Normally users arrive via an invite link (#t=…&api=…) and never see this;
// it's the manual fallback for testing or pasting credentials by hand.
export function Connect({ onConnect }: { onConnect: (c: Conn) => void }) {
  const [api, setApi] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const conn: Conn = { api: api.trim().replace(/\/+$/, ''), token: token.trim() }
    try {
      await getHealth(conn) // 200/503 both mean reachable+authorized
      saveConn(conn)
      onConnect(conn)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('That token was rejected by the hub.')
      } else {
        setError(`Could not reach the hub: ${(err as Error).message}`)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-border bg-panel p-7 shadow-2xl"
      >
        <div className="mb-6 flex items-center gap-2">
          <div className="size-8 rounded-lg bg-gradient-to-br from-accent to-accent-2" />
          <span className="text-xl font-semibold tracking-tight">dots</span>
        </div>
        <h1 className="text-base font-medium">Connect to a pool</h1>
        <p className="mt-1 text-sm text-muted">
          Paste your hub address and invite token, or open the invite link you were
          given.
        </p>

        <label className="mt-5 block text-xs text-muted">Hub address</label>
        <input
          value={api}
          onChange={(e) => setApi(e.target.value)}
          placeholder="https://hub.example.com"
          className="mt-1 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm outline-none focus:border-accent"
        />

        <label className="mt-4 block text-xs text-muted">Invite token</label>
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          type="password"
          placeholder="dots_…"
          className="mt-1 w-full rounded-lg border border-border bg-panel-2 px-3 py-2 text-sm outline-none focus:border-accent"
        />

        {error && <p className="mt-3 text-xs text-bad">{error}</p>}

        <button
          disabled={busy || !api || !token}
          className="mt-6 w-full rounded-lg bg-gradient-to-br from-accent to-accent-2 py-2.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </form>
    </div>
  )
}
