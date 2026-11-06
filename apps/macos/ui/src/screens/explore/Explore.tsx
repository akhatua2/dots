import { useState } from 'react'
import { getInviteLink } from '../../lib/bridge'
import { fmt } from '../../lib/format'
import { findPool } from '../../lib/selectors'
import type { BridgeState } from '../../lib/store'
import { Icon } from '../../components/Icon'
import { Button } from '../../ui/Button'
import { InviteBanner } from './InviteBanner'
import { PoolCard } from './PoolCard'

export function Explore({ s }: { s: BridgeState }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('All')
  const catalog = s.catalog
  if (!catalog) return <p className="p-10 text-dim">Loading pools</p>

  const needle = query.trim().toLowerCase()
  const pools = [...catalog.pools, ...s.local.pools]
    .filter((p) => p.public)
    .filter((p) => filter === 'All' || p.tags.includes(filter))
    .filter((p) => !needle || `${p.name} ${p.model} ${p.blurb}`.toLowerCase().includes(needle))

  const invite = catalog.invite
  const invitedPool = invite && !s.local.joined.includes(invite.pool_id) ? findPool(s, invite.pool_id) : undefined

  return (
    <div className="px-[34px] pb-9 pt-[38px]">
      <div className="flex items-center justify-between">
        <h1 className="text-[25px] font-bold leading-[1.15] tracking-[-0.025em]">Explore</h1>
        <div className="flex items-center gap-4">
          <p className="flex items-center gap-2 text-[13.5px] text-[#cfcfcf]">
            <span className="size-2 rounded-full bg-dim" />
            {fmt(catalog.macs_lending_now)} Macs lending right now
          </p>
          <InviteLinkButton />
        </div>
      </div>

      {invite && invitedPool && <InviteBanner from={invite.from} text={invite.text} pool={invitedPool} />}

      <div className="mt-5 flex flex-wrap items-center gap-2.5">
        <label className="flex h-9 w-[352px] items-center gap-2.5 rounded-[10px] bg-input px-3.5 text-dim">
          <Icon name="search" size={15} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pools, models, places"
            className="w-full bg-transparent text-[14px] text-ink placeholder:text-faint"
          />
        </label>
        {catalog.filters.map((name) => (
          <FilterChip key={name} name={name} active={filter === name} onSelect={setFilter} />
        ))}
      </div>

      <div className="mt-5 grid grid-cols-3 gap-[18px]">
        {pools.map((pool) => (
          <PoolCard key={pool.id} pool={pool} s={s} />
        ))}
        {pools.length === 0 && <p className="col-span-full py-16 text-center text-dim">No pools match.</p>}
      </div>
    </div>
  )
}

function FilterChip({ name, active, onSelect }: { name: string; active: boolean; onSelect: (n: string) => void }) {
  return (
    <button
      onClick={() => onSelect(name)}
      className={`h-[28px] rounded-full px-3.5 text-[13.5px] transition-colors ${
        active ? 'bg-[#f4f4f4] font-medium text-[#141414]' : 'bg-surface-2 text-[#cfcfcf] hover:bg-line-2'
      }`}
    >
      {name}
    </button>
  )
}

/** Copies a link a friend can use to join your pool. */
function InviteLinkButton() {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(await getInviteLink())
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <Button onClick={() => void copy()} className="flex items-center gap-2">
      <Icon name="link" size={15} /> {copied ? 'Copied' : 'Invite Link'}
    </Button>
  )
}
