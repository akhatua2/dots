// Connection to a dots hub: where it lives + the shared join token.
//
// The token arrives in the invite *link* as a URL fragment (#t=…&api=…), which
// the browser never sends to a server. We read it once, persist it, and strip
// it from the address bar so it doesn't linger in history. After that the app
// reads the saved connection from localStorage.

export interface Conn {
  api: string // base URL of the hub HTTP API, e.g. https://hub.example.com
  token: string // shared join token (Bearer)
}

const LS_KEY = 'dots.conn'

function defaultApi(): string {
  // Baked at build time (VITE_DOTS_API). The deployed web app ships pointing at
  // its hub so users never type an address.
  return (import.meta.env.VITE_DOTS_API as string | undefined)?.trim() || ''
}

function defaultToken(): string {
  // Baked at build time (VITE_DOTS_TOKEN) so a plain visit just works — no
  // paste screen. An invite link still overrides it.
  return (import.meta.env.VITE_DOTS_TOKEN as string | undefined)?.trim() || ''
}

function normalizeApi(api: string): string {
  return api.trim().replace(/\/+$/, '')
}

export function loadConn(): Conn | null {
  // 1) Invite link fragment wins.
  const hash = window.location.hash.replace(/^#/, '')
  if (hash) {
    const params = new URLSearchParams(hash)
    const token = params.get('t')
    if (token) {
      const conn: Conn = {
        token,
        api: normalizeApi(params.get('api') || defaultApi()),
      }
      saveConn(conn)
      // Wipe the token out of the URL.
      history.replaceState(null, '', window.location.pathname + window.location.search)
      return conn
    }
  }
  // 2) Previously saved in this browser.
  const raw = localStorage.getItem(LS_KEY)
  if (raw) {
    try {
      const conn = JSON.parse(raw) as Conn
      if (conn.token && conn.api) return { ...conn, api: normalizeApi(conn.api) }
    } catch {
      /* ignore corrupt storage */
    }
  }
  // 3) Baked-in default (this build ships pointing at its hub).
  const api = defaultApi()
  const token = defaultToken()
  if (api && token) {
    const conn = { api: normalizeApi(api), token }
    saveConn(conn)
    return conn
  }
  return null
}

export function saveConn(conn: Conn): void {
  localStorage.setItem(LS_KEY, JSON.stringify({ ...conn, api: normalizeApi(conn.api) }))
}

export function clearConn(): void {
  localStorage.removeItem(LS_KEY)
}
