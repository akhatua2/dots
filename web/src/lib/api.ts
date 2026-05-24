// Typed client for the hub's OpenAI-compatible HTTP API.
import type { Conn } from './config'

export interface Health {
  ready: boolean
  workers: number
  models: string[]
}

export interface Account {
  account_id: string
  alias: string | null
  earned_lifetime: number
  spent_lifetime: number
  turns_as_querier: number
  turns_as_worker: number
  balance: number
  balance_cu: number
  earned_cu: number
  spent_cu: number
}

export interface Turn {
  conv_id?: string
  querier?: string
  spent: number
  spent_cu: number
  [k: string]: unknown
}

export interface Credits {
  version: number
  unit: string
  accounts: Record<string, Account>
  turns: Turn[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function authHeaders(conn: Conn): Record<string, string> {
  // ngrok-skip-browser-warning suppresses ngrok's free-tier interstitial so
  // fetch() gets the real JSON/stream instead of an HTML click-through page.
  const h: Record<string, string> = { 'ngrok-skip-browser-warning': 'true' }
  if (conn.token) h.Authorization = `Bearer ${conn.token}`
  return h
}

async function getJson<T>(conn: Conn, path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${conn.api}${path}`, {
    headers: authHeaders(conn),
    signal,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new ApiError(res.status, body?.error?.message || res.statusText)
  }
  return res.json() as Promise<T>
}

export const getHealth = (conn: Conn, signal?: AbortSignal) =>
  getJson<Health>(conn, '/healthz', signal)

export const listModels = (conn: Conn, signal?: AbortSignal) =>
  getJson<{ data: { id: string }[] }>(conn, '/v1/models', signal).then((r) =>
    r.data.map((m) => m.id),
  )

export const getCredits = (conn: Conn, signal?: AbortSignal) =>
  getJson<Credits>(conn, '/v1/credits', signal)

export interface Geo {
  ip: string | null
  lat: number
  lng: number
  city: string | null
  country: string | null
}

export interface Node {
  id: string
  alias: string | null
  ready: boolean
  backend: string | null
  model: string | null
  layer_range: [number, number] | null
  geo: Geo | null
}

export const getNodes = (conn: Conn, signal?: AbortSignal) =>
  getJson<{ nodes: Node[] }>(conn, '/v1/pool/nodes', signal).then((r) => r.nodes)

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant'
  content: string
}

// Stream a chat completion, calling onToken for each content delta. Returns
// when the stream ends. Throws ApiError on a non-2xx or an in-stream error.
export async function streamChat(
  conn: Conn,
  opts: {
    model: string
    messages: ChatMessage[]
    maxTokens?: number
    temperature?: number
    onToken: (delta: string) => void
    signal?: AbortSignal
  },
): Promise<void> {
  const res = await fetch(`${conn.api}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(conn) },
    body: JSON.stringify({
      model: opts.model,
      messages: opts.messages,
      max_tokens: opts.maxTokens ?? 512,
      temperature: opts.temperature ?? 0.7,
      stream: true,
    }),
    signal: opts.signal,
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null)
    throw new ApiError(res.status, body?.error?.message || res.statusText)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // SSE frames are separated by a blank line.
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (data === '[DONE]') return
        try {
          const json = JSON.parse(data)
          if (json.error) throw new ApiError(500, json.error.message || 'stream error')
          const delta = json.choices?.[0]?.delta?.content
          if (delta) opts.onToken(delta)
        } catch (e) {
          if (e instanceof ApiError) throw e
          // ignore partial/non-JSON keepalive lines
        }
      }
    }
  }
}
