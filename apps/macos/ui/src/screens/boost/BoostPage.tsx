import { getInviteLink } from '../../lib/bridge'
import { store, type BridgeState } from '../../lib/store'
import { findPool } from '../../lib/selectors'
import { Avatar } from '../../components/Avatar'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import { Sheet } from '../../ui/Sheet'
import { CalendarTile } from '../pool/BoostBanner'
import { BoostRing } from './BoostRing'

/** A planned boost: everyone's memory together until the bigger model fits. */
export function BoostPage({ s, id }: { s: BridgeState; id: string }) {
  const pool = findPool(s, id)
  const boost = pool?.detail?.boost
  if (!pool || !boost) return <p className="p-10 text-dim">There is no boost planned.</p>

  const counted = s.local.boosting.includes(boost.id)
  const mine = s.lend?.gb ?? 0
  const pledged = boost.pledged_gb + (counted ? mine : 0)
  const toGo = Math.max(0, boost.goal_gb - pledged)

  return (
    <Sheet
      width={560}
      footer={
        <>
          <Button variant="ghost" className="mr-auto" onClick={() => store.nav({ name: 'pool', id })}>
            Back
          </Button>
          <Button onClick={() => void getInviteLink().then((link) => navigator.clipboard.writeText(link))}>Bring a Booster</Button>
          <Button variant="primary" className="!py-2.5" onClick={() => store.toggleBoost(boost.id)}>
            {counted ? 'Boosting ✓' : 'Count Me In'}
          </Button>
        </>
      }
    >
      <div className="flex items-center gap-4">
        <CalendarTile day={boost.day} date={boost.date} size="lg" />
        <div>
          <p className="text-[11px] font-semibold tracking-[0.18em] text-signal-text">BOOST</p>
          <h1 className="text-[26px] font-bold leading-tight tracking-[-0.025em]">{boost.title}</h1>
          <p className="text-[13.5px] text-dim">
            {boost.long_date} · {boost.window} · planned by {boost.planned_by}
          </p>
        </div>
      </div>

      <p className="mt-5 text-[15px] text-[#e0e0e0]">
        Reach {boost.goal_gb} GB together, and {pool.name} runs {boost.model} until 6 PM.
      </p>

      <div className="mt-4 grid place-items-center rounded-2xl border border-line bg-[#0c0c0c] py-6">
        <BoostRing filled={pledged / boost.goal_gb} label={String(pledged)} caption={`of ${boost.goal_gb} GB`} />
        <p className="mt-3 text-[14px] font-semibold">{toGo} GB to go</p>
      </div>

      <Card className="mt-4 p-4">
        <p className="flex items-baseline justify-between">
          <span className="text-[14.5px] font-semibold">Boosting</span>
          <span className="text-[13px] text-dim">{boost.boosters.length + (counted ? 1 : 0)} Macs</span>
        </p>
        <ul className="mt-3 flex flex-wrap gap-5">
          {[...boost.boosters, ...(counted ? [{ name: 'You', gb: mine, in: true }] : [])].map((b) => (
            <li key={b.name} className="text-center">
              <Avatar letter={b.name} size={44} dashed={!b.in} />
              <p className="mt-1 text-[12.5px] text-dim">{b.gb} GB</p>
            </li>
          ))}
        </ul>
      </Card>
    </Sheet>
  )
}
