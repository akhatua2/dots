// Pool + profile content. Seeded from data/seed.json (the design's content)
// until the hub serves pools, endorsements and badges.
export interface PoolModel {
  name: string
  at_gb: number
  unlock_on_join?: boolean
  params?: string
}

export interface Pool {
  id: string
  name: string
  model: string
  macs: number
  blurb: string
  lending: number
  tps: number
  earn: number
  friends: string[]
  note?: string
  endorsements: number
  endorsements_label: string
  tags: string[]
  public: boolean
  capacity_gb: number
  lenders: string[]
  can_run: PoolModel[]
}

export interface Badge {
  id: string
  icon: string
  title: string
  body: string
  when: string
  new?: boolean
}

export interface KeyRow {
  id: string
  name: string
  scope: string
  tail: string
  usage: string
  meta: string
}

/** Everything the UI renders, served by the bridge (real hub data, plus the
 *  design's preview content while the hub lacks those features). */
export interface Catalog {
  preview: boolean
  macs_lending_now: number
  filters: string[]
  models: string[]
  joined_default: string[]
  pools: Pool[]
  badges: Badge[]
  profile: { questions_helped: number; since: string }
  key_scopes: { id: string; title: string; body: string; default: boolean }[]
  default_monthly_limit: number
  keys_default: KeyRow[]
  request_split: { label: string; pct: number; note: string; shade: string }[]
}

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US')

export function shortModel(id: string): string {
  const tail = id.split('/').pop() || id
  return tail.replace(/-4bit$/i, '').replace(/-Instruct$/i, '')
}

/** A pool as the user sees it: their Mac adds one lender and its lent memory. */
export function poolView(p: Pool, joined: boolean, lentGb: number): Pool {
  if (!joined || !p.public) return p
  return {
    ...p,
    macs: p.macs + 1,
    lending: p.lending + 1,
    capacity_gb: p.capacity_gb + lentGb,
    lenders: [...p.lenders.slice(0, 6), 'You'],
  }
}

/** Models a pool can run at its current capacity, in unlock order. */
export function runnable(p: Pool) {
  return p.can_run.map((m) => ({ ...m, unlocked: p.capacity_gb >= m.at_gb }))
}
