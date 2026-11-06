import { creditsOf, store, type BridgeState } from '../../lib/store'
import { fmt } from '../../lib/format'
import { joinedPools, isLiveIn } from '../../lib/selectors'
import { PoolMark } from '../../components/Fingerprint'
import { Icon } from '../../components/Icon'
import { SectionLabel } from '../../ui/SectionLabel'
import { Stat } from '../../ui/Stat'

export function Profile({ s }: { s: BridgeState }) {
  const catalog = s.catalog
  if (!catalog) return <p className="p-10 text-dim">Loading profile</p>

  const name = s.account?.name || s.account?.alias || 'You'
  const credits = creditsOf(s.usage, s.config)
  const pools = joinedPools(s)
  const lentGb = s.lend?.gb ?? 0

  return (
    <div className="max-w-[760px] px-[34px] pb-10 pt-[38px]">
      <header className="flex items-center gap-4">
        <span className="grid size-[60px] place-items-center rounded-full bg-signal text-[26px] font-semibold text-[#1a0a03]">
          {name[0]?.toUpperCase()}
        </span>
        <div>
          <h1 className="text-[26px] font-bold leading-[1.15] tracking-[-0.025em]">{name}</h1>
          <p className="text-[13.5px] text-dim">
            {s.lend?.device.name ?? 'Mac'}
            {catalog.profile.since && ` · lending since ${catalog.profile.since}`}
          </p>
        </div>
      </header>

      {catalog.recap && !s.local.recapSeen && (
        <button
          onClick={() => store.nav({ name: 'recap' })}
          className="mt-6 flex w-full items-center gap-3 rounded-xl border border-signal/40 bg-signal-soft px-4 py-3 text-left"
        >
          <span className="size-2 rounded-full bg-signal" />
          <span className="flex-1 text-[14.5px] font-medium">Your night in {catalog.recap.window.split(' to ')[0]} is ready</span>
          <span className="text-[13.5px] text-signal-text">Open</span>
        </button>
      )}

      <div className="mt-7 flex divide-x divide-line border-y border-line py-4">
        <Stat value={credits.balance === null ? '—' : fmt(credits.balance)} label="credits" />
        <Stat value={credits.earned === null ? '—' : fmt(credits.earned)} label="earned, all time" />
        <Stat value={fmt(catalog.profile.questions_helped)} label="questions helped" />
        <Stat value={String(s.local.endorsed.length)} label="pools endorsed" />
      </div>

      <SectionLabel className="mt-8">Badges</SectionLabel>
      <ul className="mt-2 divide-y divide-line">
        {catalog.badges.map((badge) => (
          <li key={badge.id} className="flex items-center gap-4 py-3.5">
            <span className="grid size-10 shrink-0 place-items-center rounded-full bg-surface-2">
              <Icon name={badge.icon} size={18} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 text-[15.5px] font-semibold leading-tight">
                {badge.title}
                {badge.new && (
                  <span className="rounded-md bg-signal-soft px-1.5 py-0.5 text-[10.5px] font-bold tracking-wide text-signal-text">NEW</span>
                )}
              </p>
              <p className="text-[13.5px] text-dim">{badge.body}</p>
            </div>
            <span className="text-[13px] text-dim">{badge.when}</span>
          </li>
        ))}
      </ul>

      <SectionLabel className="mt-8">Pools</SectionLabel>
      <ul className="mt-2 divide-y divide-line">
        {pools.map((pool) => (
          <li key={pool.id}>
            <button
              onClick={() => store.nav({ name: 'pool', id: pool.id })}
              className="flex w-full items-center gap-3.5 py-3 text-left"
            >
              <PoolMark seed={pool.id} size={36} />
              <span className="flex-1">
                <span className="block text-[15px] font-semibold leading-tight">{pool.name}</span>
                <span className="text-[13px] text-dim">
                  {isLiveIn(s, pool.id) ? `Lending ${lentGb} GB` : 'Member'} · {pool.model}
                </span>
              </span>
              <Icon name="chevron" size={15} className="-rotate-90 text-faint" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
