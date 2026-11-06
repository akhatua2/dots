import { store, type BridgeState } from '../../lib/store'
import { findPool } from '../../lib/selectors'
import { Fingerprint } from '../../components/Fingerprint'
import { Icon } from '../../components/Icon'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import { Sheet } from '../../ui/Sheet'
import { Stat } from '../../ui/Stat'
import { NightTimeline } from './NightTimeline'

/** "Good morning": what this Mac lent overnight, and what it earned. */
export function RecapPage({ s }: { s: BridgeState }) {
  const recap = s.catalog?.recap
  const pool = recap ? findPool(s, recap.pool_id) : undefined
  if (!recap || !pool) return <p className="p-10 text-dim">Nothing to report yet.</p>

  const name = s.account?.name || s.account?.alias || 'there'
  const device = s.lend?.device.name ?? 'Mac'
  const done = () => {
    store.markRecapSeen()
    store.nav({ name: 'explore' })
  }

  return (
    <Sheet
      width={640}
      footer={
        <>
          <Button variant="ghost" className="px-4" onClick={done}>
            Done
          </Button>
          <Button
            variant="primary"
            className="!py-2.5"
            onClick={() => {
              store.markRecapSeen()
              store.nav({ name: 'pool', id: pool.id })
            }}
          >
            Open {pool.name}
          </Button>
        </>
      }
    >
      <h1 className="text-[28px] font-bold tracking-[-0.03em]">Good morning, {name}</h1>
      <p className="mt-1 text-[14px] text-dim">Last night in {pool.name}</p>

      <div className="mt-5 grid grid-cols-[0.85fr_1.2fr] gap-4">
        <section className="flex flex-col rounded-2xl border border-line bg-[#0c0c0c] p-3">
          <div className="grid flex-1 place-items-center">
            <Fingerprint seed={pool.id} size={210} rings={26} fan={0.8} animate />
          </div>
          <p className="flex items-center justify-between px-1.5 pb-1 text-[13px]">
            <span>
              <b className="font-semibold">No. {recap.number}</b> <span className="text-dim">{recap.date}</span>
            </span>
            <Icon name="download" size={15} className="text-dim" />
          </p>
        </section>

        <Card className="flex flex-col p-5">
          <p className="flex items-baseline justify-between text-[14px]">
            <span className="font-semibold">Your {device}</span>
            <span className="text-[13px] text-dim">{recap.window}</span>
          </p>
          <p className="num mt-5 flex items-baseline gap-2 text-signal-text">
            <span className="text-[52px] font-semibold leading-none tracking-[-0.03em]">+{recap.credits}</span>
            <span className="text-[15px] text-[#cfcfcf]">credits earned</span>
          </p>
          <div className="mt-5 flex divide-x divide-line border-t border-line pt-4">
            <Stat value={recap.lent} label="lent" />
            <Stat value={String(recap.runs)} label="runs" />
            <Stat value={recap.tokens} label="tokens" />
          </div>
          <p className="mt-auto flex items-center gap-3 border-t border-line pt-3.5 text-[14px]">
            <span className="grid size-7 place-items-center rounded-full bg-surface-2 text-[11px] font-semibold">S</span>
            <span className="flex-1">{recap.thanks}</span>
            <Icon name="heart" size={15} className="text-signal" />
          </p>
        </Card>
      </div>

      <div className="mt-4">
        <NightTimeline night={recap.night} />
      </div>
    </Sheet>
  )
}
