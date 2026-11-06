// Client for the Python bridge, reached through Tauri commands/events (the
// Rust shell proxies them over stdio to backend/bridge.py). No HTTP here.
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { Catalog } from './pools'

export interface Status {
  ready: boolean
  error: string | null
  model: string
}

export interface Account {
  account_id: string
  alias: string | null
  name: string | null
  email: string | null
  model: string
}

export interface Usage {
  balance_cu?: number
  earned_cu?: number
  spent_cu?: number
  credits_per_cu?: number
  earn_cu_per_hour?: number | null
  [k: string]: unknown
}

export interface AppConfig {
  api_base: string
  pool_name: string
  credits_per_cu: number
}

export interface LendState {
  device: { name: string; gb: number }
  enabled: boolean
  gb: number
  max_gb: number
  reserve_gb: number
  plugged_only: boolean
  pause_in_use: boolean
  paused: boolean
  lending: boolean
  reason: 'off' | 'paused' | 'unplugged' | 'in_use' | 'lending'
}

export type LendPatch = Partial<
  Pick<LendState, 'enabled' | 'gb' | 'plugged_only' | 'pause_in_use'> & { pause_minutes: number }
>

export interface PoolNode {
  id: string
  account_id: string | null
  alias: string | null
  ready: boolean
  backend: string | null
  device: string | null
  lat: number | null
  lng: number | null
  city: string | null
  country: string | null
  earned_cu: number // lifetime
  spent_cu: number // lifetime
  ratio: number | null // earned/(earned+spent); null = no activity yet
}

export interface Receipt {
  pool: string
  model_id: string | null
  tps: number | null
  macs: { name: string; ready: boolean }[]
  private: boolean
  free: boolean
  reasons: { icon: 'lock' | 'dots'; text: string }[]
}

// Request/response calls. The Rust `call` command forwards the method (and
// optional params) to the bridge and returns its result (or throws its error).
const call = <T>(method: string, params?: Record<string, unknown>) =>
  invoke<T>('call', { method, params })

export const getStatus = () => call<Status>('status')
export const getAccount = () => call<Account>('account')
export const getUsage = () => call<Usage>('usage')
export const getConfig = () => call<AppConfig>('config')
export const getCatalog = () => call<Catalog>('catalog')
export const getLend = () => call<LendState>('lend_get')
export const setLend = (patch: LendPatch) => call<LendState>('lend_set', patch)
export const getNodes = () => call<{ nodes: PoolNode[] }>('nodes').then((r) => r.nodes)

export type ChatEvent =
  | { chat: 'token'; text: string }
  | {
      chat: 'done'
      text: string
      decode_tokens?: number
      model_id?: string
      tps?: number | null
      receipt?: Receipt
    }
  | { chat: 'status'; state: string }
  | { chat: 'error'; error: string }

export interface ChatContext {
  name: string
  text: string
}

// Fire a prompt; tokens/terminal events arrive via onChat (Tauri events).
export const sendChat = (prompt: string, context?: ChatContext | null, maxTokens = 256) =>
  invoke('chat', { prompt, maxTokens, context })

export const onChat = (handler: (e: ChatEvent) => void): Promise<UnlistenFn> =>
  listen<ChatEvent>('chat', (e) => handler(e.payload))
export const onLend = (handler: (s: LendState) => void): Promise<UnlistenFn> =>
  listen<LendState>('lend', (e) => handler(e.payload))
export const onAskShown = (handler: () => void): Promise<UnlistenFn> =>
  listen('ask-shown', () => handler())
export const onTrayShown = (handler: () => void): Promise<UnlistenFn> =>
  listen('tray-shown', () => handler())
export const onFileDrop = (handler: (paths: string[]) => void): Promise<UnlistenFn> =>
  listen<{ paths: string[] }>('tauri://drag-drop', (e) => handler(e.payload.paths))

export const readTextFile = (path: string) => invoke<string>('read_text_file', { path })
export const openMain = () => invoke('open_main')
