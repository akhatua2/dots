import { store, type BridgeState } from '../../lib/store'
import { whoIsHere } from '../../lib/format'
import { isLiveIn, withMyMac } from '../../lib/selectors'
import type { Pool } from '../../lib/pools'
import { Avatar } from '../../components/Avatar'
import { PoolMark } from '../../components/Fingerprint'
import { Icon } from '../../components/Icon'
import { ModelMark } from '../../components/ModelMark'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'

/** One public pool in Explore. Rows only appear when there is something true to say. */
export function PoolCard({ pool: raw, s }: { pool: Pool; s: BridgeState }) {
  const live = isLiveIn(s, raw.id)
  const pool = withMyMac(raw, live, s.lend?.gb ?? 0)
  const member = s.local.joined.includes(pool.id)

  return (
    <Card
      onClick={() => store.nav({ name: 'pool', id: pool.id })}
      className="flex flex-col p-4 transition-colors hover:border-line-2"
    >
      <div className="flex items-center gap-3">
        <PoolMark seed={pool.id} size={43} />
        <div className="min-w-0">
          <p className="truncate text-[16.5px] font-semibold leading-tight">{pool.name}</p>
          <p className="flex items-center gap-1.5 truncate text-[13.5px] text-dim">
            <ModelMark model={pool.model} size={15} />
            {pool.model} · {pool.macs} {pool.macs === 1 ? 'Mac' : 'Macs'}
          </p>
        </div>
      </div>

      <p className="mt-3.5 min-h-[38px] text-[14px] leading-[1.4] text-[#b9b9b9]">{pool.blurb}</p>

      <div className="mt-2.5 space-y-2 text-[12px]">
        <p className="flex items-center gap-4">
          <span className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-dim" /> {pool.lending} lending
          </span>
          {pool.tps > 0 && (
            <span className="flex items-center gap-2 text-dim">
              <Icon name="gauge" size={14} /> {pool.tps} tok/s for you
            </span>
          )}
        </p>
        {pool.earn > 0 && (
          <p className="flex items-center gap-2">
            <Icon name="credits" size={14} /> About {pool.earn} credits an hour for your Mac
          </p>
        )}
      </div>

      <FriendsRow pool={pool} live={live} lentGb={s.lend?.gb ?? 0} />

      <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
        {pool.endorsements > 0 ? (
          <span className="flex items-center gap-2 text-[12px]">
            <Icon name="shield" size={15} className="text-dim" /> {pool.endorsements_label} endorsements
          </span>
        ) : (
          <span />
        )}
        {member ? (
          <span className="flex items-center gap-2 rounded-lg bg-btn px-3 py-[7px] text-[14px] font-medium">
            <span className="size-2 rounded-full bg-signal" /> Lending
          </span>
        ) : (
          <Button
            onClick={(e) => {
              e.stopPropagation()
              store.join(pool.id)
            }}
          >
            Add My Mac
          </Button>
        )}
      </div>
    </Card>
  )
}

function FriendsRow({ pool, live, lentGb }: { pool: Pool; live: boolean; lentGb: number }) {
  const text = live ? (
    <span className="text-[#cfcfcf]">You're lending {lentGb} GB here.</span>
  ) : pool.friends.length > 0 ? (
    <>
      <span className="flex -space-x-1.5">
        {pool.friends.slice(0, 2).map((name) => (
          <Avatar key={name} letter={name[0]} size={26} />
        ))}
      </span>
      <span className="text-[#cfcfcf]">{whoIsHere(pool.friends)}</span>
    </>
  ) : pool.note ? (
    <span className="text-[#cfcfcf]">{pool.note}</span>
  ) : (
    <span className="text-faint">No friends here yet</span>
  )
  return <p className="mt-2.5 flex min-h-[26px] items-center gap-2 text-[12.5px]">{text}</p>
}
