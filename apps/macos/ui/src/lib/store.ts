// All bridge state + side effects live here, outside React. The component
// reads it with useSyncExternalStore and calls send() — no effects in the UI.
import {
  getAccount,
  getNodes,
  getStatus,
  getUsage,
  onChat,
  sendChat,
  type Account,
  type ChatEvent,
  type PoolNode,
} from './bridge'

export interface Msg {
  role: 'user' | 'assistant'
  text: string
  pending?: boolean
  tps?: number | null // tok/s, set on the assistant message when done
}

export type Phase = 'connecting' | 'ready' | 'error'

export interface BridgeState {
  phase: Phase
  error: string | null
  account: Account | null
  balance: number | null
  msgs: Msg[]
  busy: boolean
  nodes: PoolNode[]
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

class BridgeStore {
  private state: BridgeState = {
    phase: 'connecting',
    error: null,
    account: null,
    balance: null,
    msgs: [],
    busy: false,
    nodes: [],
  }
  private listeners = new Set<() => void>()
  private started = false
  // The worker needs a few seconds after connecting to load its model layers;
  // until then chat requests come back as "pool loading". Auto-retry through
  // that warmup so the user only sends once.
  private pendingPrompt: string | null = null
  private warmupTries = 0
  private streaming = false // have real tokens started for the current round?
  private static readonly MAX_WARMUP = 15

  // useSyncExternalStore wiring. The first subscriber kicks off the bridge
  // polling/streaming (once).
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

  private start() {
    if (this.started) return
    this.started = true
    void onChat((e) => this.onChat(e))
    void this.connectLoop()
    void this.nodesLoop()
  }

  // Poll the worker map fast (2s) so contribution/usage feel near-live. It's a
  // small hub request. (A true push would be a hub WS/SSE stream — see notes.)
  private async nodesLoop() {
    for (;;) {
      await this.refreshNodes()
      await delay(2000)
    }
  }

  // One-shot map refresh — also called when the map opens for an instant update.
  async refreshNodes() {
    const nodes = await getNodes().catch(() => null)
    if (nodes) this.set({ nodes })
  }

  // Poll status until the bridge connects to the hub (or errors).
  private async connectLoop() {
    for (;;) {
      try {
        const s = await getStatus()
        if (s.error) {
          this.set({ phase: 'error', error: s.error })
          return
        }
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

  // Refresh the CU balance for the lifetime of the app.
  private async usageLoop() {
    for (;;) {
      const u = await getUsage().catch(() => null)
      if (u && typeof u.balance_cu === 'number') this.set({ balance: u.balance_cu })
      await delay(15000)
    }
  }

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
      // First token of the round replaces any "warming up…" placeholder.
      if (!this.streaming) {
        this.streaming = true
        this.setLastAssistant({ text: e.text, pending: true })
      } else {
        const last = this.state.msgs[this.state.msgs.length - 1]
        this.setLastAssistant({ text: (last?.text ?? '') + e.text })
      }
    } else if (e.chat === 'done') {
      this.setLastAssistant({ pending: false, tps: e.tps ?? null }, { busy: false })
      this.finish()
    } else if (e.chat === 'status') {
      // Pool not generation-ready yet (worker still loading). Retry quietly.
      if (this.pendingPrompt && this.warmupTries < BridgeStore.MAX_WARMUP) {
        this.warmupTries += 1
        this.setLastAssistant({ text: 'warming up the pool…', pending: true })
        setTimeout(() => this.fire(), 4000)
      } else {
        this.setLastAssistant(
          { text: `pool ${e.state} — try again in a moment.`, pending: false },
          { busy: false },
        )
        this.finish()
      }
    } else if (e.chat === 'error') {
      this.setLastAssistant({ text: `error: ${e.error}`, pending: false }, { busy: false })
      this.finish()
    }
  }

  private fire() {
    if (!this.pendingPrompt) return
    sendChat(this.pendingPrompt).catch((err) =>
      this.onChat({ chat: 'error', error: String(err) }),
    )
  }

  send(prompt: string) {
    const p = prompt.trim()
    if (!p || this.state.busy) return
    this.pendingPrompt = p
    this.warmupTries = 0
    this.set({
      busy: true,
      msgs: [
        ...this.state.msgs,
        { role: 'user', text: p },
        { role: 'assistant', text: '', pending: true },
      ],
    })
    this.fire()
  }
}

export const store = new BridgeStore()
