import { useState } from 'react'
import { store, type BridgeState } from '../lib/store'
import { fmt, type Pool } from '../lib/pools'
import { Avatar } from '../components/Avatar'
import { Icon } from '../components/Icon'
import { Swirl } from '../components/Swirl'

export function Explore({ s }: { s: BridgeState }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('All')
  const c = s.catalog
  if (!c) return <p className="p-10 text-dim">Loading pools</p>
  const pools = [...c.pools, ...s.local.pools].filter((p) => p.public)
  const q = query.trim().toLowerCase()
  const shown = pools.filter(
    (p) =>
      (filter === 'All' || p.tags.includes(filter)) &&
      (!q || `${p.name} ${p.model} ${p.blurb}`.toLowerCase().includes(q)),
  )
  const featured = pools.find((p) => p.note && !s.local.joined.includes(p.id))?.id

  return (
    <div className="px-10 pb-10 pt-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-[32px] font-bold leading-tight tracking-[-0.025em]">Explore</h1>
          <p className="mt-1.5 text-[15px] text-dim">
            Public pools you can lend to. Your Mac earns credits in every one.
          </p>
        </div>
        <p className="mt-3 flex items-center gap-2 text-[14px] text-[#cfcfcf]">
          <span className="size-2 rounded-full bg-dim" />
          {fmt(c.macs_lending_now)} Macs lending right now
        </p>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <label className="flex h-11 w-[340px] items-center gap-3 rounded-xl bg-input px-4 text-dim">
          <Icon name="search" size={17} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pools, models, places"
            className="w-full bg-transparent text-[14.5px] text-ink outline-none placeholder:text-faint"
          />
        </label>
        {c.filters.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`h-9 rounded-full px-4 text-[14px] transition-colors ${
              filter === f ? 'bg-[#f4f4f4] font-medium text-[#141414]' : 'bg-surface-2 text-[#cfcfcf] hover:bg-line-2'
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(290px,1fr))] gap-5">
        {shown.map((p) => (
          <PoolCard key={p.id} p={p} s={s} featured={p.id === featured} />
        ))}
        {shown.length === 0 && <p className="col-span-full py-16 text-center text-dim">No pools match.</p>}
      </div>
    </div>
  )
}

function PoolCard({ p, s, featured }: { p: Pool; s: BridgeState; featured: boolean }) {
  const joined = s.local.joined.includes(p.id)
  const lending = joined && s.local.lendingTo === p.id
  const lentGb = s.lend?.gb ?? 0
  return (
    <div
      onClick={() => store.nav({ name: 'pool', id: p.id })}
      className={`card flex flex-col p-5 transition-colors hover:border-line-2 ${featured ? '!border-line-2' : ''}`}
    >
      <div className="flex items-center gap-3.5">
        <Swirl seed={p.id} size={44} cells={26} />
        <div className="min-w-0">
          <p className="truncate text-[17px] font-semibold leading-tight">{p.name}</p>
          <p className="truncate text-[13.5px] text-dim">
            {p.model} · {p.macs + (lending ? 1 : 0)} Macs
          </p>
        </div>
      </div>
      <p className="mt-4 min-h-[44px] text-[14.5px] leading-snug text-[#b9b9b9]">{p.blurb}</p>
      <div className="mt-3 flex items-center gap-5 text-[13.5px]">
        <span className="flex items-center gap-2 text-ink">
          <span className="size-2 rounded-full bg-dim" /> {p.lending + (lending ? 1 : 0)} lending
        </span>
        <span className="flex items-center gap-2 text-dim">
          <Icon name="gauge" size={15} /> {p.tps} tok/s for you
        </span>
      </div>
      <p className="mt-2 flex items-center gap-2 text-[13.5px] text-ink">
        <Icon name="credits" size={15} /> About {p.earn} credits an hour for your Mac
      </p>
      <p className="mt-3 flex min-h-[28px] items-center gap-2 text-[13.5px]">
        {lending ? (
          <span className="text-[#cfcfcf]">You&apos;re lending {lentGb} GB here.</span>
        ) : p.friends.length ? (
          <>
            <span className="flex -space-x-1.5">
              {p.friends.slice(0, 2).map((f) => (
                <Avatar key={f} letter={f[0]} size={26} />
              ))}
            </span>
            <span className="text-[#cfcfcf]">{friends(p.friends)} here</span>
          </>
        ) : p.note ? (
          <span className="text-[#cfcfcf]">{p.note}</span>
        ) : (
          <span className="text-faint">No friends here yet</span>
        )}
      </p>
      <div className="mt-4 flex items-center justify-between border-t border-line pt-4">
        <span className="flex items-center gap-2 text-[13.5px] text-ink">
          <Icon name="shield" size={16} className="text-dim" /> {p.endorsements_label} endorsements
        </span>
        {joined ? (
          <span className="flex items-center gap-2 rounded-lg bg-btn px-3.5 py-2 text-[14px] font-medium">
            <span className="size-2 rounded-full bg-signal" /> Lending
          </span>
        ) : (
          <button
            onClick={(e) => {
              e.stopPropagation()
              store.join(p.id)
            }}
            className={`rounded-lg px-3.5 py-2 text-[14px] font-semibold transition-opacity hover:opacity-90 ${
              featured ? 'bg-signal text-[#1a0a03]' : 'bg-btn text-ink'
            }`}
          >
            Add my Mac
          </button>
        )}
      </div>
    </div>
  )
}

const friends = (n: string[]) => (n.length === 1 ? n[0] : `${n.slice(0, -1).join(', ')} and ${n[n.length - 1]}`)
