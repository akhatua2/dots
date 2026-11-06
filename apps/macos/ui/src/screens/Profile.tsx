import { creditsOf, type BridgeState } from '../lib/store'
import { fmt, poolView, shortModel } from '../lib/pools'
import { Icon } from '../components/Icon'
import { Swirl } from '../components/Swirl'

export function Profile({ s }: { s: BridgeState }) {
  const name = s.account?.name || s.account?.alias || 'You'
  const credits = creditsOf(s.usage, s.config)
  const c = s.catalog
  if (!c) return <p className="p-10 text-dim">Loading profile</p>
  const pools = [...c.pools, ...s.local.pools].filter((p) => s.local.joined.includes(p.id))
  const lentGb = s.lend?.gb ?? 0
  const stats = [
    { label: 'Credits', value: credits.balance === null ? '—' : fmt(credits.balance) },
    { label: 'Earned, all time', value: credits.earned === null ? '—' : fmt(credits.earned) },
    { label: 'Questions helped', value: fmt(c.profile.questions_helped) },
    { label: 'Pools endorsed', value: String(s.local.endorsed.length) },
  ]
  return (
    <div className="px-10 pb-10 pt-8">
      <div className="flex items-center gap-5">
        <span className="grid size-[68px] place-items-center rounded-full bg-signal text-[30px] font-semibold text-[#1a0a03]">
          {name[0]?.toUpperCase()}
        </span>
        <div>
          <h1 className="text-[32px] font-bold leading-tight tracking-[-0.025em]">{name}</h1>
          <p className="text-[15px] text-dim">
            {s.lend?.device.name ?? 'Mac'} · {c.profile.since ? `lending since ${c.profile.since} · ` : ''}{pools.length}{' '}
            {pools.length === 1 ? 'pool' : 'pools'}
          </p>
        </div>
      </div>

      <div className="mt-7 grid grid-cols-4 gap-4">
        {stats.map((x) => (
          <div key={x.label} className="card px-5 py-4">
            <p className="text-[14px] text-dim">{x.label}</p>
            <p className="mt-1 text-[32px] font-bold leading-tight tracking-[-0.025em]">{x.value}</p>
          </div>
        ))}
      </div>

      <p className="label mt-8">Badges</p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        {c.badges.map((b) => (
          <div
            key={b.id}
            className={`flex items-center gap-4 rounded-2xl border bg-card px-5 py-4 ${
              b.new ? 'border-signal' : 'border-line'
            }`}
          >
            <span className="grid size-12 shrink-0 place-items-center rounded-full bg-surface-2">
              <Icon name={b.icon} size={20} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[17px] font-semibold leading-tight">{b.title}</p>
              <p className="text-[14px] text-dim">{b.body}</p>
            </div>
            <div className="flex flex-col items-end gap-1.5">
              <span className="text-[13.5px] text-dim">{b.when}</span>
              {b.new && (
                <span className="rounded-md bg-signal-soft px-2 py-0.5 text-[11.5px] font-bold tracking-wide text-signal-text">
                  NEW
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <p className="label mt-8">Pools</p>
      <div className="mt-3 grid grid-cols-3 gap-4">
        {pools.map((raw) => {
          const lending = s.local.lendingTo === raw.id
          const p = poolView(raw, lending, lentGb)
          return (
            <div key={p.id} className="card flex items-center gap-3.5 px-4 py-3.5">
              <Swirl seed={p.id} size={36} cells={22} />
              <div className="min-w-0">
                <p className="truncate text-[15.5px] font-semibold leading-tight">{p.name}</p>
                <p className="truncate text-[13px] text-dim">
                  {lending ? `Lending ${lentGb} GB` : 'Member'} · {shortModel(p.model)}
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
