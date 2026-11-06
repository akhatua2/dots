// All bridge state + side effects live here, outside React. Components read it
// with useSyncExternalStore and call methods on it — no effects in the UI.
import {
  askFrontier,
  getAccount,
  getCatalog,
  getConfig,
  getLend,
  getNodes,
  getStatus,
  getUsage,
  onAskShown,
  onChat,
  onFileDrop,
  onLend,
  onTrayShown,
  readTextFile,
  sendChat,
  setLend,
  type Account,
  type AppConfig,
  type ChatContext,
  type ChatEvent,
  type LendPatch,
  type LendState,
  type PoolNode,
  type Receipt,
  type Usage,
} from './bridge'
import type { Catalog, Pool } from './pools'

export interface Msg {
  role: 'user' | 'assistant'
  text: string
  pending?: boolean
  tps?: number | null
  receipt?: Receipt | null
  attachment?: string | null
  frontier?: { label: string; tokens: number } | null
}

export type Phase = 'connecting' | 'ready' | 'error'
export type Route =
  | { name: 'explore' }
  | { name: 'api' }
  | { name: 'profile' }
  | { name: 'pool'; id: string }

export interface ApiKey {
  id: string
  name: string
  scope: string
  tail: string
  usage: string
  meta: string
  isNew?: boolean
}

export interface JoinMoment {
  stage: 'in' | 'unlock'
  poolId: string
  n: number
  model?: string
  params?: string
  capacity: number
  poolName: string
}

interface Local {
  seeded: boolean // joined/keys taken from the catalog defaults once
  joined: string[]
  endorsed: string[]
  pools: Pool[]
  keys: ApiKey[]
  lendingTo: string | null
}

export interface BridgeState {
  surface: string
  phase: Phase
  error: string | null
  account: Account | null
  config: AppConfig | null
  catalog: Catalog | null
  usage: Usage | null
  lend: LendState | null
  nodes: PoolNode[]
  route: Route
  local: Local
  join: JoinMoment | null
  msgs: Msg[]
  busy: boolean
  attachment: ChatContext | null
  focusTick: number
  creditPulse: { amount: number; at: number } | null
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))
const STORAGE = 'dots.ui.v1'

function loadLocal(): Local {
  const fresh: Local = { seeded: false, joined: [], endorsed: [], pools: [], keys: [], lendingTo: null }
  try {
    const raw = localStorage.getItem(STORAGE)
    return raw ? { ...fresh, ...JSON.parse(raw) } : fresh
  } catch {
    return fresh
  }
}

const surface = document.documentElement.dataset.surface ?? 'main'

class BridgeStore {
  private state: BridgeState = {
    surface,
    phase: 'connecting',
    error: null,
    account: null,
    config: null,
    catalog: null,
    usage: null,
    lend: null,
    nodes: [],
    route: { name: 'explore' },
    local: loadLocal(),
    join: null,
    msgs: [],
    busy: false,
    attachment: null,
    focusTick: 0,
    creditPulse: null,
  }
  private listeners = new Set<() => void>()
  private started = false
  // The worker needs a few seconds after connecting to load its model layers;
  // until then chat requests come back as "pool loading". Auto-retry through
  // that warmup so the user only sends once.
  private pendingPrompt: string | null = null
  private warmupTries = 0
  private streaming = false
  private static readonly MAX_WARMUP = 15

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb)
    this.start()
    return () => {
      this.listeners.delete(cb)
    }
  }

  getSnapshot = (): BridgeState => this.state

  private set(patch: Partial<BridgeState>) {
    this.state = { ...this.state, ...patch }
    for (const l of this.listeners) l()
  }

  private setLocal(patch: Partial<Local>) {
    const local = { ...this.state.local, ...patch }
    try {
      localStorage.setItem(STORAGE, JSON.stringify(local))
    } catch {
      /* storage unavailable */
    }
    this.set({ local })
  }

  private start() {
    if (this.started) return
    this.started = true
    void onChat((e) => this.onChat(e))
    void onLend((lend) => this.set({ lend }))
    void onTrayShown(() => void this.refresh())
    void onAskShown(() => this.askShown())
    void onFileDrop((paths) => void this.attachPath(paths[0]))
    // Another window changed local state (joined a pool, made a key…).
    window.addEventListener('storage', () => this.set({ local: loadLocal() }))
    void this.connectLoop()
    if (surface === 'main') void this.nodesLoop()
    else void this.refreshCatalog()
  }

  // Poll the pool map data every 2s; it is a small hub request.
  private async nodesLoop() {
    for (;;) {
      await this.refreshNodes()
      await delay(2000)
    }
  }

  async refreshNodes() {
    const nodes = await getNodes().catch(() => null)
    if (nodes) this.set({ nodes })
    await this.refreshCatalog()
  }

  async refreshCatalog() {
    const catalog = await getCatalog().catch(() => null)
    if (!catalog) return
    this.set({ catalog })
    // First catalog: adopt its defaults (pools already joined, existing keys).
    const l = this.state.local
    if (!l.seeded) {
      this.setLocal({
        seeded: true,
        joined: catalog.joined_default,
        keys: catalog.keys_default.map((k) => ({ ...k })),
        lendingTo: l.lendingTo ?? (catalog.joined_default.includes('hub') ? 'hub' : null),
      })
    }
  }

  private async connectLoop() {
    for (;;) {
      try {
        const s = await getStatus()
        if (s.error) {
          this.set({ phase: 'error', error: s.error })
          return
        }
        const [config, lend] = await Promise.all([getConfig(), getLend()])
        this.set({ config, lend })
        if (s.ready) {
          const account = await getAccount().catch(() => null)
          this.set({ phase: 'ready', account })
          void this.usageLoop()
          return
        }
      } catch {
        /* bridge still coming up */
      }
      await delay(800)
    }
  }

  private async usageLoop() {
    for (;;) {
      await this.refresh()
      await delay(15000)
    }
  }

  async refresh() {
    const usage = await getUsage().catch(() => null)
    if (usage) {
      // A rise in the balance flashes "+N" beside it in the menu bar dropdown.
      const per = usage.credits_per_cu ?? this.state.config?.credits_per_cu ?? 0
      const before = this.state.usage?.balance_cu
      const rise =
        typeof before === 'number' && typeof usage.balance_cu === 'number'
          ? Math.round((usage.balance_cu - before) * per)
          : 0
      this.set({ usage, creditPulse: rise > 0 ? { amount: rise, at: Date.now() } : this.state.creditPulse })
    }
    const lend = await getLend().catch(() => null)
    if (lend) this.set({ lend })
  }

  // --- navigation ----------------------------------------------------------
  nav(route: Route) {
    this.set({ route })
  }

  // --- lending (menu bar dropdown) ----------------------------------------
  async lendSet(patch: LendPatch) {
    const cur = this.state.lend
    if (cur) {
      const { pause_minutes, ...rest } = patch
      this.set({
        lend: { ...cur, ...rest, paused: pause_minutes !== undefined ? pause_minutes > 0 : cur.paused },
      })
    }
    const lend = await setLend(patch).catch(() => null)
    if (lend) this.set({ lend })
  }

  // --- pools ---------------------------------------------------------------
  allPools(): Pool[] {
    return [...(this.state.catalog?.pools ?? []), ...this.state.local.pools]
  }

  join(poolId: string) {
    const l = this.state.local
    if (l.joined.includes(poolId)) return
    const pool = this.allPools().find((p) => p.id === poolId)
    if (!pool) return
    this.setLocal({ joined: [...l.joined, poolId], lendingTo: poolId })
    void this.lendSet({ enabled: true })
    const gb = this.state.lend?.gb ?? 0
    const capacity = pool.capacity_gb + gb
    const unlock = pool.can_run.find((m) => m.unlock_on_join && capacity >= m.at_gb)
    this.set({
      join: { stage: 'in', poolId, n: pool.macs + 1, capacity, poolName: pool.name },
    })
    void (async () => {
      await delay(2600)
      if (unlock) {
        this.set({
          join: {
            stage: 'unlock',
            poolId,
            n: pool.macs + 1,
            capacity,
            poolName: pool.name,
            model: unlock.name,
            params: unlock.params,
          },
        })
        await delay(3200)
      }
      this.set({ join: null })
    })()
  }

  createPool(name: string, model: string) {
    const id = `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${Date.now().toString(36)}`
    const gb = this.state.lend?.gb ?? 0
    const pool: Pool = {
      id,
      name,
      model,
      macs: 1,
      blurb: 'Your pool.',
      lending: 1,
      tps: 0,
      earn: 0,
      friends: [],
      endorsements: 0,
      endorsements_label: '0',
      tags: [],
      public: false,
      capacity_gb: gb,
      lenders: ['You'],
      can_run: [{ name: model, at_gb: 0 }],
    }
    const l = this.state.local
    this.setLocal({ pools: [...l.pools, pool], joined: [...l.joined, id], lendingTo: id })
    this.set({ route: { name: 'pool', id } })
  }

  toggleEndorse(poolId: string) {
    const l = this.state.local
    this.setLocal({
      endorsed: l.endorsed.includes(poolId)
        ? l.endorsed.filter((x) => x !== poolId)
        : [...l.endorsed, poolId],
    })
  }

  // --- API keys --------------------------------------------------------------
  createKey(name: string, scope: string): string {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    const secret = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    const key = `dots_sk_live_${secret}`
    const k: ApiKey = {
      id: `k-${Date.now()}`,
      name: name || 'Untitled',
      scope,
      tail: key.slice(-4),
      usage: 'Not used yet',
      meta: '',
      isNew: true,
    }
    this.setLocal({ keys: [k, ...this.state.local.keys] })
    return key
  }

  revokeKey(id: string) {
    this.setLocal({ keys: this.state.local.keys.filter((k) => k.id !== id) })
  }

  // --- chat ------------------------------------------------------------------
  private setLastAssistant(patch: Partial<Msg>, extra: Partial<BridgeState> = {}) {
    const msgs = this.state.msgs.slice()
    const i = msgs.length - 1
    if (msgs[i]?.role === 'assistant') msgs[i] = { ...msgs[i], ...patch }
    this.set({ msgs, ...extra })
  }

  private finish() {
    this.pendingPrompt = null
    this.warmupTries = 0
    this.streaming = false
  }

  private onChat(e: ChatEvent) {
    if (e.chat === 'token') {
      if (!this.streaming) {
        this.streaming = true
        this.setLastAssistant({ text: e.text, pending: true })
      } else {
        const last = this.state.msgs[this.state.msgs.length - 1]
        this.setLastAssistant({ text: (last?.text ?? '') + e.text })
      }
    } else if (e.chat === 'done') {
      this.setLastAssistant(
        { pending: false, tps: e.tps ?? null, receipt: e.receipt ?? null },
        { busy: false },
      )
      this.finish()
    } else if (e.chat === 'status') {
      if (this.pendingPrompt && this.warmupTries < BridgeStore.MAX_WARMUP) {
        this.warmupTries += 1
        this.setLastAssistant({ text: 'Choosing where to run it', pending: true })
        setTimeout(() => this.fire(), 4000)
      } else {
        this.setLastAssistant(
          { text: `The pool is ${e.state}. Try again in a moment.`, pending: false },
          { busy: false },
        )
        this.finish()
      }
    } else if (e.chat === 'error') {
      this.setLastAssistant({ text: `Error: ${e.error}`, pending: false }, { busy: false })
      this.finish()
    }
  }

  private fire() {
    if (!this.pendingPrompt) return
    sendChat(this.pendingPrompt, this.state.attachment).catch((err) =>
      this.onChat({ chat: 'error', error: String(err) }),
    )
  }

  send(prompt: string) {
    const p = prompt.trim()
    if (!p || this.state.busy) return
    this.pendingPrompt = p
    this.warmupTries = 0
    const att = this.state.attachment
    this.set({
      busy: true,
      msgs: [
        ...this.state.msgs,
        { role: 'user', text: p, attachment: att?.name ?? null },
        { role: 'assistant', text: 'Choosing where to run it', pending: true },
      ],
    })
    this.fire()
  }

  /** Second opinion from a frontier model: paid, runs outside the pools. */
  async askClaude() {
    const msgs = this.state.msgs
    const q = [...msgs].reverse().find((m) => m.role === 'user')
    if (!q || this.state.busy) return
    this.set({
      busy: true,
      msgs: [...msgs, { role: 'assistant', text: 'Choosing where to run it', pending: true }],
    })
    try {
      const r = await askFrontier(q.text, this.state.attachment)
      this.setLastAssistant(
        { text: r.text, pending: false, frontier: { label: r.label, tokens: r.tokens } },
        { busy: false },
      )
    } catch (e) {
      this.setLastAssistant({ text: `Error: ${String(e)}`, pending: false }, { busy: false })
    }
  }

  // The Ask panel starts fresh each time it opens.
  private askShown() {
    if (surface !== 'ask') return
    this.set({ msgs: [], busy: false, attachment: null, focusTick: this.state.focusTick + 1 })
    this.finish()
  }

  async attachPath(path?: string) {
    if (!path) return
    const text = await readTextFile(path).catch(() => null)
    if (text === null) return
    this.set({ attachment: { name: path.split('/').pop() || path, text } })
  }

  clearAttachment() {
    this.set({ attachment: null })
  }
}

export const store = new BridgeStore()

/** Credits shown to the user: ledger compute units scaled by config. */
export function creditsOf(usage: Usage | null, config: AppConfig | null) {
  const per = usage?.credits_per_cu ?? config?.credits_per_cu ?? 0
  return {
    balance: typeof usage?.balance_cu === 'number' ? usage.balance_cu * per : null,
    earned: typeof usage?.earned_cu === 'number' ? usage.earned_cu * per : null,
    perHour:
      typeof usage?.earn_cu_per_hour === 'number' ? usage.earn_cu_per_hour * per : null,
  }
}
