// All bridge state + side effects live here, outside React. Components read it
// with useSyncExternalStore and call methods on it — no effects in the UI.
import {
  askFrontier,
  getAccount,
  createGatewayKey,
  getCatalog,
  getGateway,
  revokeGatewayKey,
  getConfig,
  getLend,
  getNodes,
  getStatus,
  getUiState,
  setUiState,
  getUsage,
  onAskInline,
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
  type GatewayView,
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
  | { name: 'settings' }
  | { name: 'ask' }
  | { name: 'recap' }
  | { name: 'pool'; id: string }
  | { name: 'invite'; id: string }
  | { name: 'boost'; id: string }

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
  lendingTo: string | null
  boosting: string[] // boosts this Mac has been counted in
  glass: boolean | null // null: follow config.toml
  recapSeen: boolean
}

export interface BridgeState {
  surface: string
  phase: Phase
  error: string | null
  account: Account | null
  config: AppConfig | null
  catalog: Catalog | null
  gateway: GatewayView | null
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
const freshLocal = (): Local => ({
  seeded: false,
  joined: [],
  endorsed: [],
  pools: [],
  lendingTo: null,
  boosting: [],
  glass: null,
  recapSeen: false,
})

const surface = document.documentElement.dataset.surface ?? 'main'

class BridgeStore {
  private state: BridgeState = {
    surface,
    phase: 'connecting',
    error: null,
    account: null,
    config: null,
    catalog: null,
    gateway: null,
    usage: null,
    lend: null,
    nodes: [],
    route: { name: 'explore' },
    local: freshLocal(),
    join: null,
    msgs: [],
    busy: false,
    attachment: null,
    focusTick: 0,
    creditPulse: null,
  }
  private listeners = new Set<() => void>()
  private started = false
  private stateLoaded = false // user state read from the backend
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
    void setUiState(local).catch(() => {})
    this.set({ local })
  }

  private start() {
    if (this.started) return
    this.started = true
    void onChat((e) => this.onChat(e))
    void onLend((lend) => this.set({ lend }))
    void onTrayShown(() => void this.refresh().then(() => this.loadState()))
    void onAskShown(() => this.askShown())
    void onAskInline(() => this.set({ route: { name: 'ask' }, focusTick: this.state.focusTick + 1 }))
    void onFileDrop((paths) => void this.attachPath(paths[0]))
    void this.loadState().then(() => {
      if (surface === 'main') void this.nodesLoop()
      else void this.refreshCatalog()
    })
    void this.connectLoop()
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
    await Promise.all([this.refreshCatalog(), this.refreshGateway()])
  }

  /** User state (joined pools, endorsements, keys) lives in the backend. */
  async loadState() {
    for (let i = 0; i < 20; i++) {
      const saved = await getUiState().catch(() => null)
      if (saved) {
        this.stateLoaded = true
        this.set({ local: { ...freshLocal(), ...(saved as Partial<Local>) } })
        return
      }
      await delay(500)
    }
    this.stateLoaded = true
  }

  async refreshCatalog() {
    if (!this.stateLoaded) return
    const catalog = await getCatalog().catch(() => null)
    if (!catalog) return
    this.set({ catalog })
    // First catalog: adopt its defaults (pools already joined, existing keys).
    const l = this.state.local
    if (!l.seeded) {
      this.setLocal({
        seeded: true,
        joined: catalog.joined_default,
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
        document.documentElement.dataset.glass = (this.state.local.glass ?? config.glass) ? 'on' : 'off'
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

  /** Accept a pool (an Explore card or an invite) and start lending `gb` to it. */
  join(poolId: string, gb?: number) {
    const l = this.state.local
    if (l.joined.includes(poolId)) return
    const pool = this.allPools().find((p) => p.id === poolId)
    if (!pool) return
    this.setLocal({ joined: [...l.joined, poolId], lendingTo: poolId })
    void this.lendSet({ enabled: true, ...(gb ? { gb } : {}) })
    const lent = gb ?? this.state.lend?.gb ?? 0
    const capacity = pool.capacity_gb + lent
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

  /** Count this Mac in on a boost, or take it back out. */
  toggleBoost(boostId: string) {
    const l = this.state.local
    this.setLocal({
      boosting: l.boosting.includes(boostId)
        ? l.boosting.filter((id) => id !== boostId)
        : [...l.boosting, boostId],
    })
  }

  /** Stop lending to a pool and drop it from "Your pools". */
  leavePool(poolId: string) {
    const l = this.state.local
    this.setLocal({
      joined: l.joined.filter((id) => id !== poolId),
      lendingTo: l.lendingTo === poolId ? null : l.lendingTo,
    })
    this.set({ route: { name: 'explore' } })
  }

  setGlass(on: boolean) {
    this.setLocal({ glass: on })
    document.documentElement.dataset.glass = on ? 'on' : 'off'
  }

  markRecapSeen() {
    this.setLocal({ recapSeen: true })
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

  // --- API keys (made and checked by the local gateway in the bridge) ----------
  async refreshGateway() {
    const gateway = await getGateway().catch(() => null)
    if (gateway) this.set({ gateway })
  }

  /** Returns the secret, which is shown once and never stored. */
  async createKey(name: string, scopes: string[], monthlyLimit: number | null): Promise<string> {
    const { secret } = await createGatewayKey(name, scopes, monthlyLimit)
    await this.refreshGateway()
    return secret
  }

  async revokeKey(id: string) {
    await revokeGatewayKey(id)
    await this.refreshGateway()
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

  /** Start a fresh question, keeping the screen. */
  newQuestion() {
    this.set({ msgs: [], busy: false, attachment: null, focusTick: this.state.focusTick + 1 })
    this.finish()
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
