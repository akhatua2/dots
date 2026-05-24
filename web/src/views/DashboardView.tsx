import { useQuery } from '@tanstack/react-query'
import { getCredits } from '../lib/api'
import type { Conn } from '../lib/config'
import { cu, shortId } from '../lib/format'

export function DashboardView({ conn }: { conn: Conn }) {
  const credits = useQuery({
    queryKey: ['credits', conn.api],
    queryFn: ({ signal }) => getCredits(conn, signal),
    refetchInterval: 5000,
  })

  const accounts = Object.values(credits.data?.accounts ?? {})
  const turns = credits.data?.turns ?? []
  const totalSpent = turns.reduce((s, t) => s + (t.spent_cu ?? 0), 0)
  const topEarner = [...accounts].sort((a, b) => b.earned_cu - a.earned_cu)[0]

  return (
    <div className="h-full overflow-y-auto">
      <header className="border-b border-border px-6 py-3">
        <h1 className="text-sm font-medium">Dashboard</h1>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-6">
        {credits.isError && (
          <p className="text-sm text-bad">Couldn't load ledger: {(credits.error as Error).message}</p>
        )}

        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat label="Compute used" value={`${cu(totalSpent)} CU`} hint="across all turns" />
          <Stat label="Turns run" value={`${turns.length}`} hint="prompts served" />
          <Stat label="Accounts" value={`${accounts.length}`} hint="in this pool" />
          <Stat
            label="Top contributor"
            value={topEarner ? `${cu(topEarner.earned_cu)} CU` : '—'}
            hint={topEarner ? shortId(topEarner.alias || topEarner.account_id) : 'no activity yet'}
          />
        </div>

        <h2 className="mt-8 mb-3 text-xs font-medium uppercase tracking-wide text-muted">
          Accounts
        </h2>
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-xs text-muted">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium">Account</th>
                <th className="px-4 py-2.5 text-right font-medium">Earned</th>
                <th className="px-4 py-2.5 text-right font-medium">Spent</th>
                <th className="px-4 py-2.5 text-right font-medium">Balance</th>
                <th className="px-4 py-2.5 text-right font-medium">Turns</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-muted">
                    No activity yet — run a chat or contribute a Mac to get started.
                  </td>
                </tr>
              )}
              {accounts.map((a) => (
                <tr key={a.account_id} className="border-t border-border">
                  <td className="px-4 py-2.5 font-mono text-xs">
                    {a.alias || shortId(a.account_id)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-good">{cu(a.earned_cu)}</td>
                  <td className="px-4 py-2.5 text-right text-muted">{cu(a.spent_cu)}</td>
                  <td
                    className={`px-4 py-2.5 text-right ${a.balance_cu >= 0 ? 'text-text' : 'text-bad'}`}
                  >
                    {cu(a.balance_cu)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-muted">
                    {a.turns_as_worker + a.turns_as_querier}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted">
          1 CU = 1 TFLOP of compute. The ledger is zero-sum: every CU spent querying is a
          CU earned by the Macs that served it.
        </p>
      </div>
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-xl border border-border bg-panel p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      <div className="mt-0.5 text-xs text-muted/70">{hint}</div>
    </div>
  )
}
