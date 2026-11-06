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
  detail?: PoolDetail
}

export interface Booster {
  name: string
  gb: number
  in: boolean
}

export interface Boost {
  id: string
  title: string
  day: string
  date: string
  long_date: string
  window: string
  planned_by: string
  model: string
  goal_gb: number
  pledged_gb: number
  boosters: Booster[]
}

export interface PoolDetail {
  level: number
  next_level: number
  level_total_gb: number
  nights: number
  runs_tonight: number
  tokens: string
  tps_live: number
  live_no: number
  owner: string
  layers: string
  members: { name: string; live: boolean }[]
  boost?: Boost
  activity: { who: string; text: string; when: string }[]
}

export interface Recap {
  pool_id: string
  window: string
  credits: number
  lent: string
  runs: number
  tokens: string
  thanks: string
  number: number
  date: string
  night: {
    ticks: string[]
    rows: { who: string; start: number; end: number; label: string; mine?: boolean }[]
  }
}

export interface ApiExtra {
  saved: {
    amount: string
    against: string
    requests: number
    free_pct: number
    credits_spent: number
    billed: string
    with_counts: string
  }
  split: { label: string; pct: number; note: string; shade: string }[]
  recent: { at: string; key: string; ran: string; tokens: string; cost: string; saved: string; note?: string }[]
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
  invite?: { from: string; pool_id: string; text: string }
  recap?: Recap
  api_extra?: ApiExtra
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

/** What the pool page needs, from the pool's own detail or derived from what it can run. */
export function levelOf(pool: Pool, capacityGb: number) {
  const next = pool.can_run.find((m) => capacityGb < m.at_gb)
  const level = pool.detail?.level ?? pool.can_run.filter((m) => capacityGb >= m.at_gb).length
  return {
    level,
    nextLevel: level + 1,
    nextModel: next?.name ?? null,
    totalGb: pool.detail?.level_total_gb ?? next?.at_gb ?? capacityGb,
    toGo: next ? Math.max(0, (pool.detail?.level_total_gb ?? next.at_gb) - capacityGb) : 0,
  }
}
