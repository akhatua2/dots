// Small read-only helpers over the store state, so screens stay declarative.
import type { BridgeState } from './store'
import type { Pool } from './pools'

export const allPools = (s: BridgeState): Pool[] => [...(s.catalog?.pools ?? []), ...s.local.pools]

export const findPool = (s: BridgeState, id: string) => allPools(s).find((p) => p.id === id)

export const joinedPools = (s: BridgeState) => allPools(s).filter((p) => s.local.joined.includes(p.id))

/** True while this Mac is actually lending to the pool (not merely a member). */
export const isLiveIn = (s: BridgeState, poolId: string) =>
  !!s.lend?.lending && s.local.lendingTo === poolId

/** This Mac's share of a pool: when it is lending there, its memory counts. */
export function withMyMac(pool: Pool, lending: boolean, lentGb: number): Pool {
  if (!lending) return pool
  return {
    ...pool,
    macs: pool.macs + 1,
    lending: pool.lending + 1,
    capacity_gb: pool.capacity_gb + lentGb,
    lenders: [...pool.lenders, 'You'],
  }
}
