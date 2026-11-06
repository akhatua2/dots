import { store, type BridgeState } from '../../lib/store'
import { findPool, isLiveIn, withMyMac } from '../../lib/selectors'
import { levelOf } from '../../lib/pools'
import { Button } from '../../ui/Button'
import { ActivityCard } from './ActivityCard'
import { BoostBanner } from './BoostBanner'
import { ContributionCard } from './ContributionCard'
import { FingerprintCard } from './FingerprintCard'
import { LevelCard } from './LevelCard'
import { PoolHeader } from './PoolHeader'

export function PoolPage({ s, id }: { s: BridgeState; id: string }) {
  const raw = findPool(s, id)
  if (!raw) return <p className="p-10 text-dim">That pool is gone.</p>

  const member = s.local.joined.includes(id)
  const live = isLiveIn(s, id)
  const lend = s.lend
  const lentGb = lend?.gb ?? 0
  const pool = withMyMac(raw, live, lentGb)
  const level = levelOf(pool, pool.capacity_gb)

  // Your worker's real layer range, when the hub has placed one.
  const mine = s.nodes.find((n) => n.account_id === s.account?.account_id)
  const layers = mine?.layer_range ? `Runs layers ${mine.layer_range[0]} to ${mine.layer_range[1]}` : (pool.detail?.layers ?? null)

  return (
    <div className="px-[34px] pb-9 pt-[34px]">
      <PoolHeader pool={pool} level={level.level} iAmLive={live} />

      {pool.detail?.boost && (
        <BoostBanner poolId={id} boost={pool.detail.boost} counted={s.local.boosting.includes(pool.detail.boost.id)} />
      )}

      <div className="mt-4 grid grid-cols-[1.15fr_1fr] gap-4">
        <FingerprintCard pool={pool} live={live} />
        <div className="space-y-4">
          <LevelCard
            level={level.nextLevel}
            nextModel={level.nextModel}
            capacityGb={pool.capacity_gb}
            totalGb={level.totalGb}
            toGo={level.toGo}
            mineGb={live ? lentGb : 0}
            canLendMore={!!lend && lend.gb < lend.max_gb}
            onLendMore={() => void store.lendSet({ gb: Math.min(lend?.max_gb ?? 0, lentGb + level.toGo) })}
          />
          {member ? (
            <ContributionCard
              enabled={!!lend?.enabled}
              lending={live}
              gb={lentGb}
              maxGb={lend?.max_gb ?? 1}
              poolGb={pool.capacity_gb}
              layers={layers}
              onToggle={(on) => void store.lendSet({ enabled: on })}
              onCommit={(gb) => void store.lendSet({ gb })}
              onLeave={() => store.leavePool(id)}
            />
          ) : (
            <Button variant="primary" className="w-full !py-3" onClick={() => store.nav({ name: 'invite', id })}>
              Add My Mac
            </Button>
          )}
          {pool.detail && <ActivityCard activity={pool.detail.activity} />}
        </div>
      </div>
    </div>
  )
}
