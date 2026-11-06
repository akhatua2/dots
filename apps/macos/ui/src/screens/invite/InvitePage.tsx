import { useState } from 'react'
import { store, type BridgeState } from '../../lib/store'
import { findPool } from '../../lib/selectors'
import { levelOf } from '../../lib/pools'
import { Avatar } from '../../components/Avatar'
import { Fingerprint } from '../../components/Fingerprint'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import { Sheet } from '../../ui/Sheet'

/** Accepting an invite: how much of this Mac to lend, and what that unlocks. */
export function InvitePage({ s, id }: { s: BridgeState; id: string }) {
  const pool = findPool(s, id)
  const lend = s.lend
  const [pick, setPick] = useState<'now' | 'spare'>('spare')
  if (!pool || !lend) return <p className="p-10 text-dim">This invite is no longer available.</p>

  const nowGb = lend.gb
  const spareGb = lend.max_gb
  const lendGb = pick === 'spare' ? spareGb : nowGb
  const level = levelOf(pool, pool.capacity_gb)
  const unlocks = level.nextModel !== null && pool.capacity_gb + lendGb >= level.totalGb
  const from = s.catalog?.invite?.from ?? pool.detail?.owner ?? 'a friend'
  const members = pool.detail?.members ?? []

  const join = () => {
    store.join(id, lendGb)
    store.nav({ name: 'pool', id })
  }

  return (
    <Sheet
      footer={
        <>
          <Button variant="ghost" className="px-4" onClick={() => store.nav({ name: 'explore' })}>
            Not Now
          </Button>
          <Button variant="primary" className="!py-2.5" onClick={join}>
            Join and Lend {lendGb} GB
          </Button>
        </>
      }
    >
      <h1 className="text-[24px] font-bold tracking-[-0.025em]">Join {pool.name}</h1>
      <p className="mt-1 text-[14px] text-dim">An invite from {from}</p>

      <Card className="mt-5 flex gap-4 p-3.5">
        <span className="grid size-[92px] shrink-0 place-items-center overflow-hidden rounded-xl bg-[#0a0a0a]">
          <Fingerprint seed={pool.id} size={96} rings={14} fan={0.7} />
        </span>
        <div className="min-w-0">
          <p className="text-[17px] font-semibold">{pool.name}</p>
          <p className="mt-0.5 text-[13.5px] text-dim">
            Level {level.level} · {pool.model} · {pool.capacity_gb} GB live
          </p>
          <p className="mt-2 flex items-center gap-2 text-[13.5px]">
            <span className="flex -space-x-1">
              {members.map((m) => (
                <span key={m.name} className="relative">
                  <Avatar letter={m.name} size={26} />
                  {m.live && <span className="absolute -bottom-px -right-px size-2 rounded-full bg-signal ring-2 ring-card" />}
                </span>
              ))}
            </span>
            {members.filter((m) => m.live).length} live
            <span className="text-dim">{members.length} members</span>
          </p>
          <p className="mt-2 text-[13.5px] text-[#cfcfcf]">{pool.blurb}</p>
        </div>
      </Card>

      <p className="mt-6 flex items-baseline justify-between">
        <span className="text-[14.5px] font-semibold">Your Mac lends</span>
        <span className="text-[13px] text-dim">
          {lend.device.name} · {lend.device.gb} GB
        </span>
      </p>
      <div className="mt-2.5 grid grid-cols-2 gap-3">
        <Choice gb={nowGb} caption="What you lend now" selected={pick === 'now'} onSelect={() => setPick('now')} />
        <Choice gb={spareGb} caption="Everything it can spare" selected={pick === 'spare'} onSelect={() => setPick('spare')} />
      </div>

      <Card className="mt-3 p-4">
        <ShareBar existing={pool.capacity_gb} mine={lendGb} total={level.totalGb} />
        <p className="mt-3 text-[15px] font-semibold text-signal-text">
          {unlocks
            ? `Your Mac unlocks ${level.nextModel} for everyone in ${pool.name}.`
            : level.nextModel
              ? `Your Mac gets ${pool.name} ${lendGb} GB closer to ${level.nextModel}.`
              : `Your Mac joins ${pool.name}.`}
        </p>
      </Card>
    </Sheet>
  )
}

function Choice({ gb, caption, selected, onSelect }: { gb: number; caption: string; selected: boolean; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className={`rounded-xl border px-4 py-3 text-left transition-colors ${
        selected ? 'border-signal/70 bg-signal-soft' : 'border-line bg-card hover:border-line-2'
      }`}
    >
      <span className="block text-[17px] font-semibold">{gb} GB</span>
      <span className="text-[13.5px] text-dim">{caption}</span>
    </button>
  )
}

/** The pool's memory so far, with your share added in orange. */
function ShareBar({ existing, mine, total }: { existing: number; mine: number; total: number }) {
  const width = (gb: number) => `${Math.min(100, (gb / Math.max(total, 1)) * 100)}%`
  return (
    <div className="flex h-2.5 gap-px overflow-hidden rounded-full bg-surface-2">
      <span className="bg-[#a8a8a8]" style={{ width: width(existing) }} />
      <span className="bg-signal" style={{ width: width(mine) }} />
    </div>
  )
}
