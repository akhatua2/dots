import { creditsOf, type BridgeState } from '../../lib/store'
import { fmt } from '../../lib/format'
import { findPool } from '../../lib/selectors'

/** The card at the foot of the sidebar: are we lending, and what has it earned. */
export function LiveCard({ s }: { s: BridgeState }) {
  const credits = creditsOf(s.usage, s.config)
  const pool = s.local.lendingTo ? findPool(s, s.local.lendingTo) : undefined
  const live = !!s.lend?.lending && !!pool
  const balance = credits.balance === null ? '—' : fmt(credits.balance)
  const perHour = credits.perHour !== null && credits.perHour >= 0.5 ? ` · +${fmt(credits.perHour)} an hour` : ''

  return (
    <div className="glass-card m-3 rounded-[14px] border border-line bg-card px-3.5 py-3">
      <p className="flex items-center gap-2.5 text-[14px] font-semibold">
        <span className={`size-2 rounded-full ${live ? 'bg-signal' : 'border border-dim'}`} />
        {live ? `Live in ${pool!.name}` : 'Not lending'}
      </p>
      <p className="mt-0.5 pl-[18px] text-[13px] text-dim">
        {balance} credits{live ? perHour : ''}
      </p>
    </div>
  )
}
