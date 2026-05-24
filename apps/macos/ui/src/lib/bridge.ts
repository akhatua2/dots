// Client for the Python bridge, reached through Tauri commands/events (the
// Rust shell proxies them over stdio to backend/bridge.py). No HTTP here.
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

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

export interface Pool {
  ready?: boolean
  nodes?: { id?: string; alias?: string | null; ready?: boolean }[]
  [k: string]: unknown
}

export interface Usage {
  balance_cu?: number
  earned_cu?: number
  spent_cu?: number
  [k: string]: unknown
}

// Request/response calls. The Rust `call` command forwards the method to the
// bridge and returns its result (or throws the bridge's error string).
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

export const getStatus = () => invoke<Status>('call', { method: 'status' })
export const getAccount = () => invoke<Account>('call', { method: 'account' })
export const getPool = () => invoke<Pool>('call', { method: 'pool' })
export const getUsage = () => invoke<Usage>('call', { method: 'usage' })
export const getNodes = () =>
  invoke<{ nodes: PoolNode[] }>('call', { method: 'nodes' }).then((r) => r.nodes)

export type ChatEvent =
  | { chat: 'token'; text: string }
  | { chat: 'done'; text: string; decode_tokens?: number; model_id?: string; tps?: number | null }
  | { chat: 'status'; state: string }
  | { chat: 'error'; error: string }

// Fire a prompt; tokens/terminal events arrive via onChat (Tauri events).
export const sendChat = (prompt: string, maxTokens = 256) =>
  invoke('chat', { prompt, maxTokens })

export const onChat = (handler: (e: ChatEvent) => void): Promise<UnlistenFn> =>
  listen<ChatEvent>('chat', (e) => handler(e.payload))
